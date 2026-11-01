"""Test audio: deterministic synthetic fixtures, or a licensed pack the operator supplies.

Two sources only, and the difference is recorded per fixture:

* **synthetic** — generated here, bit-for-bit reproducible, *not speech*. These fixtures measure
  latency, throughput, robustness and failure handling. They cannot measure word error rate,
  entity accuracy or task success, because there is nothing spoken to recognise.
* **licensed** — a pack directory supplied by the operator with a ``pack.json`` that declares, for
  every item, its file, language, transcript, expected entities, licence and source. Private or
  personal audio is refused outright: the pack validator rejects ``personal: true`` and any item
  without a licence, and nothing is ever downloaded.

A fixture is described by data, so the plan can record its hash and a re-run can prove it used the
same audio.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import struct
import wave
from dataclasses import dataclass, field
from pathlib import Path

from . import FIXTURE_SCHEMA

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2
CHANNELS = 1

#: Conditions this benchmark knows how to name. A condition is a property of the audio, not a
#: promise about the room it was recorded in.
CONDITIONS = ("clean", "noisy_10db", "quiet_-40dbfs", "clipped")

#: Licences that are acceptable for a fixture pack. Anything else (including an empty string) is
#: refused, because "I found it somewhere" is not a licence.
ACCEPTABLE_LICENCE_PREFIXES = (
    "cc0", "cc-by", "cc by", "public-domain", "public domain", "generated", "own-recording",
    "apache-2.0", "mit",
)


class FixtureError(Exception):
    """A fixture or fixture pack is unusable. Always fatal: a run never continues without audio."""


@dataclass(frozen=True)
class Fixture:
    id: str
    kind: str                       # "synthetic" | "licensed"
    path: Path
    sha256: str
    duration_s: float
    sample_rate: int
    condition: str
    language: str | None
    transcript: str | None
    entities: tuple[dict, ...] = ()
    licence: str = ""
    source: str = ""
    contains_speech: bool = False
    generator: dict = field(default_factory=dict)
    channels: int = CHANNELS

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "path": str(self.path),
            "sha256": self.sha256,
            "duration_s": self.duration_s,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "condition": self.condition,
            "language": self.language,
            "has_transcript": bool(self.transcript),
            "entities": list(self.entities),
            "licence": self.licence,
            "source": self.source,
            "contains_speech": self.contains_speech,
            "generator": dict(self.generator),
        }


# --------------------------------------------------------------------------- wav writing
def _write_wav(path: Path, samples: list[int], *, sample_rate: int = SAMPLE_RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(CHANNELS)
        handle.setsampwidth(SAMPLE_WIDTH)
        handle.setframerate(sample_rate)
        handle.writeframes(b"".join(struct.pack("<h", max(-32768, min(32767, value)))
                                    for value in samples))


def _duration(samples: list[int], sample_rate: int) -> float:
    return round(len(samples) / sample_rate, 4)


def _tone(seconds: float, frequency: float, amplitude: float = 0.25,
          sample_rate: int = SAMPLE_RATE) -> list[int]:
    total = int(seconds * sample_rate)
    peak = int(32767 * amplitude)
    return [int(peak * math.sin(2 * math.pi * frequency * index / sample_rate))
            for index in range(total)]


def _chirp(seconds: float, start_hz: float, end_hz: float, amplitude: float = 0.25,
           sample_rate: int = SAMPLE_RATE) -> list[int]:
    total = int(seconds * sample_rate)
    peak = int(32767 * amplitude)
    samples = []
    phase = 0.0
    for index in range(total):
        fraction = index / max(1, total - 1)
        frequency = start_hz + (end_hz - start_hz) * fraction
        phase += 2 * math.pi * frequency / sample_rate
        samples.append(int(peak * math.sin(phase)))
    return samples


def _white_noise(seconds: float, amplitude: float, seed: int,
                 sample_rate: int = SAMPLE_RATE) -> list[int]:
    generator = random.Random(seed)
    peak = int(32767 * amplitude)
    return [int(peak * (generator.random() * 2.0 - 1.0)) for _ in range(int(seconds * sample_rate))]


def _speechlike(seconds: float, seed: int, sample_rate: int = SAMPLE_RATE) -> list[int]:
    """Amplitude-modulated band noise with syllable-like envelopes. Explicitly *not* speech.

    It exists so a pipeline can be driven through several seconds of changing energy, which is what
    latency and streaming behaviour care about. It carries no words and the fixture says so.
    """
    generator = random.Random(seed)
    total = int(seconds * sample_rate)
    samples: list[int] = []
    for index in range(total):
        t = index / sample_rate
        envelope = 0.5 * (1.0 + math.sin(2 * math.pi * 3.1 * t))
        if (t % 0.42) < 0.08:                      # short gaps between "syllables"
            envelope *= 0.05
        carrier = math.sin(2 * math.pi * 180 * t) * 0.6
        carrier += math.sin(2 * math.pi * 640 * t) * 0.25
        carrier += math.sin(2 * math.pi * 1900 * t) * 0.15
        noise = generator.random() * 2.0 - 1.0
        samples.append(int(32767 * 0.22 * envelope * (0.85 * carrier + 0.15 * noise)))
    return samples


def _mix(signal: list[int], noise: list[int], snr_db: float) -> list[int]:
    signal_power = sum(value * value for value in signal) / max(1, len(signal))
    noise_power = sum(value * value for value in noise) / max(1, len(noise))
    if signal_power <= 0 or noise_power <= 0:
        return list(signal)
    target_noise_power = signal_power / (10 ** (snr_db / 10))
    scale = math.sqrt(target_noise_power / noise_power)
    mixed = []
    for index, value in enumerate(signal):
        noise_value = noise[index % len(noise)] if noise else 0
        mixed.append(int(max(-32768, min(32767, value + noise_value * scale))))
    return mixed


def _scale(signal: list[int], factor: float) -> list[int]:
    return [int(max(-32768, min(32767, value * factor))) for value in signal]


def _clip(signal: list[int]) -> list[int]:
    return [max(-32768, min(32767, int(value * 6.0))) for value in signal]


# --------------------------------------------------------------------------- synthetic set
SYNTHETIC_SPECS: tuple[dict, ...] = (
    {"id": "syn.tone.440hz.1s", "builder": "tone", "seconds": 1.0, "condition": "clean"},
    {"id": "syn.chirp.300-3400hz.2s", "builder": "chirp", "seconds": 2.0, "condition": "clean"},
    {"id": "syn.silence.1s", "builder": "silence", "seconds": 1.0, "condition": "clean"},
    {"id": "syn.noise.white.2s", "builder": "noise", "seconds": 2.0, "condition": "clean"},
    {"id": "syn.speechlike.3s", "builder": "speechlike", "seconds": 3.0, "condition": "clean"},
    {"id": "syn.tone.440hz.noisy10db.1s", "builder": "tone_noisy", "seconds": 1.0,
     "condition": "noisy_10db"},
    {"id": "syn.tone.440hz.quiet.1s", "builder": "tone_quiet", "seconds": 1.0,
     "condition": "quiet_-40dbfs"},
    {"id": "syn.tone.440hz.clipped.1s", "builder": "tone_clipped", "seconds": 1.0,
     "condition": "clipped"},
    {"id": "syn.speechlike.noisy10db.3s", "builder": "speechlike_noisy", "seconds": 3.0,
     "condition": "noisy_10db"},
)


def _build(name: str, seconds: float, seed: int = 20261101) -> list[int]:
    if name == "tone":
        return _tone(seconds, 440.0)
    if name == "chirp":
        return _chirp(seconds, 300.0, 3400.0)
    if name == "silence":
        return [0] * int(seconds * SAMPLE_RATE)
    if name == "noise":
        return _white_noise(seconds, 0.2, seed)
    if name == "speechlike":
        return _speechlike(seconds, seed)
    if name == "tone_noisy":
        return _mix(_tone(seconds, 440.0), _white_noise(seconds, 0.2, seed + 1), 10.0)
    if name == "tone_quiet":
        return _scale(_tone(seconds, 440.0), 0.01)
    if name == "tone_clipped":
        return _clip(_tone(seconds, 440.0, amplitude=0.4))
    if name == "speechlike_noisy":
        return _mix(_speechlike(seconds, seed), _white_noise(seconds, 0.2, seed + 2), 10.0)
    raise FixtureError(f"unknown synthetic fixture builder: {name}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def generate_synthetic(directory: Path, *, seed: int = 20261101) -> list[Fixture]:
    """Write the synthetic fixture set into ``directory`` and describe it.

    Byte-identical between runs for the same seed, which is what makes the plan hash meaningful.
    """
    fixtures: list[Fixture] = []
    for spec in SYNTHETIC_SPECS:
        samples = _build(spec["builder"], float(spec["seconds"]), seed=seed)
        path = directory / f"{spec['id']}.wav"
        _write_wav(path, samples)
        fixtures.append(
            Fixture(
                id=spec["id"],
                kind="synthetic",
                path=path,
                sha256=sha256_file(path),
                duration_s=_duration(samples, SAMPLE_RATE),
                sample_rate=SAMPLE_RATE,
                condition=spec["condition"],
                language=None,
                transcript=None,
                entities=(),
                licence="generated locally by the benchmark (no third-party audio)",
                source="synthetic generator in app/bench/fixtures.py",
                contains_speech=False,
                generator={"builder": spec["builder"], "seed": seed,
                           "sample_rate": SAMPLE_RATE},
            )
        )
    return fixtures


# --------------------------------------------------------------------------- licensed pack
def _normalise_licence(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split())


def load_pack(directory: Path, *, allowed_languages: set[str]) -> list[Fixture]:
    """Load an operator-supplied licensed fixture pack.

    Refusals, all fatal: missing pack file, unknown schema, missing licence, ``personal: true``,
    a file outside the pack directory, an unknown language, an empty transcript, a duplicate id, or
    audio that is not a readable PCM WAV.
    """
    directory = Path(directory)
    manifest_path = directory / "pack.json"
    if not manifest_path.is_file():
        raise FixtureError(f"no pack.json in {directory}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FixtureError(f"pack.json is not valid JSON: {exc}") from exc
    if manifest.get("schema") != FIXTURE_SCHEMA:
        raise FixtureError(f"pack.json schema must be {FIXTURE_SCHEMA!r}")
    items = manifest.get("items")
    if not isinstance(items, list) or not items:
        raise FixtureError("pack.json has no items")

    fixtures: list[Fixture] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        label = item.get("id") or f"item[{index}]"
        if not isinstance(item, dict):
            raise FixtureError(f"{label}: item must be an object")
        if item.get("personal") is True or item.get("contains_personal_data") is True:
            raise FixtureError(
                f"{label}: personal audio is refused; this benchmark uses synthetic or licensed audio only"
            )
        licence = _normalise_licence(item.get("licence"))
        if not licence:
            raise FixtureError(f"{label}: a licence is required (e.g. 'CC0-1.0')")
        if not any(licence.startswith(prefix) for prefix in ACCEPTABLE_LICENCE_PREFIXES):
            raise FixtureError(
                f"{label}: licence {item.get('licence')!r} is not in the accepted set "
                f"({', '.join(ACCEPTABLE_LICENCE_PREFIXES)})"
            )
        language = str(item.get("language") or "").strip()
        if language not in allowed_languages:
            raise FixtureError(
                f"{label}: language {language!r} is not in the proposed matrix; Q19 is open, so the "
                "benchmark may not extend the list"
            )
        transcript = item.get("transcript")
        if not isinstance(transcript, str) or not transcript.strip():
            raise FixtureError(f"{label}: a transcript is required to measure word error rate")
        for entity in item.get("entities", []):
            if not isinstance(entity, dict) or not entity.get("text") or not entity.get("kind"):
                raise FixtureError(f"{label}: each entity needs 'kind' and 'text'")
        fixture_id = str(item.get("id") or "").strip()
        if not fixture_id:
            raise FixtureError(f"{label}: id is required")
        if fixture_id in seen:
            raise FixtureError(f"{label}: duplicate id in the pack")
        seen.add(fixture_id)

        relative = Path(str(item.get("file") or ""))
        if not relative.parts or relative.is_absolute() or ".." in relative.parts:
            raise FixtureError(f"{label}: 'file' must be a relative path inside the pack")
        path = (directory / relative).resolve()
        if directory.resolve() not in path.parents or not path.is_file():
            raise FixtureError(f"{label}: audio file is missing or outside the pack: {relative}")
        try:
            with wave.open(str(path), "rb") as handle:
                frames = handle.getnframes()
                rate = handle.getframerate()
                channels = handle.getnchannels()
                width = handle.getsampwidth()
        except (wave.Error, EOFError) as exc:
            raise FixtureError(f"{label}: {relative} is not a readable PCM WAV ({exc})") from exc
        if width != SAMPLE_WIDTH:
            raise FixtureError(f"{label}: {relative} must be 16-bit PCM")
        if rate <= 0 or frames <= 0:
            raise FixtureError(f"{label}: {relative} has no audio")

        condition = str(item.get("condition") or "clean")
        if condition not in CONDITIONS:
            raise FixtureError(f"{label}: condition must be one of {', '.join(CONDITIONS)}")

        fixtures.append(
            Fixture(
                id=fixture_id,
                kind="licensed",
                path=path,
                sha256=sha256_file(path),
                duration_s=round(frames / rate, 4),
                sample_rate=rate,
                channels=channels,
                condition=condition,
                language=language,
                transcript=transcript.strip(),
                entities=tuple(
                    {"kind": str(entity["kind"]), "text": str(entity["text"])}
                    for entity in item.get("entities", [])
                ),
                licence=str(item.get("licence")),
                source=str(item.get("source") or "operator-supplied"),
                contains_speech=True,
                generator={},
            )
        )
    return fixtures


def fixture_counts_by_language(fixtures: list[Fixture]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for fixture in fixtures:
        if fixture.language:
            counts[fixture.language] = counts.get(fixture.language, 0) + 1
    return counts
