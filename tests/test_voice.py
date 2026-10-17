"""Voice adapter (R2) tested with simulated Termux:API commands on PATH.

These fakes emulate the documented behaviour (partial recognitions on stdout; text to
speak on stdin). They prove the integration path, not real Android speech quality."""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

from conftest import run_cli
from jarvis.voice import speakable, voice_loop

STT = """#!{python}
import sys
from pathlib import Path
queue = Path({queue!r})
lines = queue.read_text().splitlines() if queue.exists() else []
if not lines:
    sys.exit(0)
utterance, rest = lines[0], lines[1:]
queue.write_text("\\n".join(rest) + ("\\n" if rest else ""))
words = utterance.split()
for i in range(1, len(words)):
    print(" ".join(words[:i]))          # partial recognitions
print(utterance)
"""
TTS = """#!{python}
import sys
from pathlib import Path
with Path({log!r}).open("a") as f:
    f.write(sys.stdin.read() + "\\n---\\n")
"""


def install_fakes(tmp_path: Path, utterances: list[str]) -> tuple[Path, Path]:
    bin_dir, queue, log = tmp_path / "fakebin", tmp_path / "queue.txt", tmp_path / "spoken.log"
    bin_dir.mkdir()
    queue.write_text("\n".join(utterances) + "\n")
    for name, body in (("termux-speech-to-text", STT.format(python=sys.executable, queue=str(queue))),
                       ("termux-tts-speak", TTS.format(python=sys.executable, log=str(log)))):
        path = bin_dir / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return bin_dir, log


def test_spoken_conversation_through_the_real_cli(tmp_path):
    bin_dir, log = install_fakes(tmp_path, ["remember that the pump draws 2A at 12V",
                                            "what do you know about the pump?", "goodbye jarvis"])
    home = tmp_path / "home"
    proc = run_cli(home, "voice", "--max-turns", "5",
                   env_extra={"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"})
    assert proc.returncode == 0, proc.stderr
    assert "Voice session saved (2 turn(s))" in proc.stdout
    spoken = log.read_text()
    assert "Noted as memory 1" in spoken and "the pump draws 2A at 12V" in spoken
    assert "Okay. Your session is saved." in spoken
    assert "•" not in spoken and "#" not in spoken                     # symbols stripped for speech
    import sqlite3
    with sqlite3.connect(home / "jarvis.sqlite3") as db:
        channels = {r[0] for r in db.execute("SELECT payload_json FROM events WHERE kind='observation.received'")}
    assert all('"channel":"voice"' in c for c in channels)


def test_voice_unavailable_is_reported_honestly(tmp_path):
    proc = run_cli(tmp_path / "home", "voice", env_extra={"PATH": "/usr/bin:/bin"})
    assert proc.returncode != 0 and "Voice is unavailable" in proc.stderr and "termux-speech-to-text" in proc.stderr


def test_speakable_strips_symbols_and_bounds_length():
    assert speakable("✓ task #1 done • next: **write** firmware") == "task 1 done next: write firmware"
    long = ". ".join(f"Sentence number {i}" for i in range(200))
    out = speakable(long)
    assert len(out) <= 640 and out.endswith("The rest is on screen.")


def test_loop_leaves_after_repeated_silence():
    class Silent:
        def listen(self):
            return None

        def speak(self, text):
            return True

    messages = []
    summary = voice_loop(lambda *a, **k: {}, Silent(), max_silences=2, echo=messages.append)
    assert summary == {"turns": 0, "session_id": None} and "leaving voice mode" in messages[-1]
