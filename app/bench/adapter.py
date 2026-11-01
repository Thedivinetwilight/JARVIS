"""The candidate-adapter contract, its hashes, and why there is no default.

An adapter is a **single Python file** the operator names explicitly::

    ADAPTER = {
        "name": "some-local-recogniser",       # free text, recorded
        "version": "0.3.0",                    # free text, recorded
        "kind": "asr",                         # "asr" | "tts" | "turn"
        "languages": ["en", "hi"],             # the adapter *claims*; the benchmark measures
        "streaming": False,                    # True only if the *_stream function exists
        "notes": "optional free text",
    }

    def transcribe(request: dict) -> dict:      # kind == "asr"
        return {"text": "..."}                 # optional "language"

    def synthesise(request: dict) -> dict:      # kind == "tts"
        return {"audio_bytes": 88200, "sample_rate": 22050}

    def respond(request: dict) -> dict:         # kind == "turn" (optional)
        return {"text": "..."}

Streaming variants, if and only if ``ADAPTER["streaming"]`` is true, may be named
``transcribe_stream`` / ``synthesise_stream`` and must yield dicts; the first yielded item marks
first output, the last marks completion.

Rules the harness enforces, so an adapter cannot quietly become a network client or a model
download:

* the file must exist and be readable, and its sha256 is recorded before it is imported;
* the model path must exist **locally**; nothing is downloaded, and a hash mismatch against the
  hash the operator supplied is fatal;
* the module is imported inside the sandboxed worker, after every socket and look-up is denied;
* a declared ``ADAPTER["languages"]`` claim is recorded as a *claim*, never as a result.

There is no default adapter and no discovery: without an explicit path the benchmark refuses.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

#: Bytes of a file we are willing to hash in full inside a model *directory*. Larger files are
#: recorded by name and size only, and the mode is stated in the result.
LARGE_FILE_THRESHOLD = 64 * 1024 * 1024
MAX_TREE_FILES = 20000

KINDS = ("asr", "tts", "turn")


class AdapterError(Exception):
    """The adapter or model cannot be used. Always fatal for the run."""


@dataclass
class Artifact:
    """A file or directory the operator pointed at, with the identity evidence we could get."""

    given_path: str
    display_path: str
    kind: str                     # "adapter" | "model" | "fixture_pack"
    path_type: str                # "file" | "directory"
    exists: bool
    size_bytes: int | None = None
    file_count: int | None = None
    sha256: str | None = None
    hash_mode: str | None = None  # "content" | "tree_content" | "tree_metadata" | "not_computed"
    operator_declared_sha256: str | None = None
    verified: bool | None = None
    problems: list = field(default_factory=list)

    def to_dict(self) -> dict:
        # Only the display path is published: the raw path the operator typed may name their home
        # directory, and a result document is meant to be shareable.
        return {
            "display_path": self.display_path,
            "kind": self.kind,
            "path_type": self.path_type,
            "exists": self.exists,
            "size_bytes": self.size_bytes,
            "file_count": self.file_count,
            "sha256": self.sha256,
            "hash_mode": self.hash_mode,
            "operator_declared_sha256": self.operator_declared_sha256,
            "verified": self.verified,
            "problems": list(self.problems),
        }


def display_path(path: Path, *, repo_root: Path | None = None, home: Path | None = None) -> str:
    """A path safe to publish: the repository and the home directory are replaced by tokens."""
    resolved = Path(path).resolve()
    root = (repo_root or Path(__file__).resolve().parents[2]).resolve()
    home_dir = (home or Path(os.path.expanduser("~"))).resolve()
    try:
        return "<repo>/" + str(resolved.relative_to(root))
    except ValueError:
        pass
    try:
        return "~/" + str(resolved.relative_to(home_dir))
    except ValueError:
        return str(resolved)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_digest(directory: Path) -> tuple[str, str, int, int]:
    """Digest a model directory. Returns (sha256, mode, size, files).

    Small/medium files are hashed by content; files above the threshold contribute their relative
    path and size only, and the mode says so. Deterministic either way.
    """
    entries: list[str] = []
    total_size = 0
    files = 0
    metadata_only = False
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        files += 1
        if files > MAX_TREE_FILES:
            raise AdapterError(
                f"model directory has more than {MAX_TREE_FILES} files; point at the model files "
                "directly or split the model"
            )
        size = path.stat().st_size
        total_size += size
        relative = str(path.relative_to(directory))
        if size > LARGE_FILE_THRESHOLD:
            entries.append(f"{relative}\t{size}\tlarge")
            metadata_only = True
        else:
            entries.append(f"{relative}\t{size}\t{_sha256_file(path)}")
    digest = hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()
    return digest, ("tree_metadata" if metadata_only else "tree_content"), total_size, files


def describe_artifact(
    given: str | Path,
    *,
    kind: str,
    declared_sha256: str | None = None,
    compute_hash: bool = True,
) -> Artifact:
    """Describe and (optionally) hash an adapter or model path. Never follows a symlink out of a model."""
    path = Path(given).expanduser()
    artifact = Artifact(
        given_path=str(given),
        display_path=display_path(path),
        kind=kind,
        path_type="directory" if path.is_dir() else "file",
        exists=path.exists(),
        operator_declared_sha256=declared_sha256.strip().lower() if declared_sha256 else None,
    )
    if not path.exists():
        artifact.problems.append("path does not exist; nothing is downloaded by this benchmark")
        artifact.verified = False
        return artifact

    if path.is_dir():
        try:
            digest, mode, size, files = _tree_digest(path)
        except AdapterError as exc:
            artifact.problems.append(str(exc))
            artifact.verified = False
            return artifact
        artifact.size_bytes = size
        artifact.file_count = files
        artifact.hash_mode = mode if compute_hash else "not_computed"
        artifact.sha256 = digest if compute_hash else None
    else:
        artifact.size_bytes = path.stat().st_size
        artifact.file_count = 1
        artifact.hash_mode = "content" if compute_hash else "not_computed"
        artifact.sha256 = _sha256_file(path) if compute_hash else None

    if artifact.operator_declared_sha256:
        if artifact.sha256 is None:
            artifact.verified = None
        else:
            artifact.verified = artifact.sha256 == artifact.operator_declared_sha256
            if not artifact.verified:
                artifact.problems.append(
                    "supplied sha256 does not match the local artifact; refusing to measure it"
                )
    return artifact


def require_artifacts(adapters: dict[str, Artifact], models: dict[str, Artifact]) -> list[str]:
    """Collect every reason the supplied adapter/model set cannot be measured."""
    problems: list[str] = []
    for name, artifact in adapters.items():
        if not artifact.exists:
            problems.append(f"{name} adapter: {artifact.display_path} does not exist")
        elif artifact.verified is False:
            problems.append(f"{name} adapter: sha256 mismatch")
    for name, artifact in models.items():
        if not artifact.exists:
            problems.append(f"{name} model: {artifact.display_path} does not exist")
        elif artifact.verified is False:
            problems.append(f"{name} model: sha256 mismatch")
    return problems
