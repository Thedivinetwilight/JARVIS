"""Small shared helpers: time, identifiers, JSON, and text bounding.

Kept dependency-free so the runtime still installs on constrained platforms
(Android/Termux) where compiled wheels are unavailable.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    """Current time as an aware UTC datetime (the default clock)."""
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def age_days(value: str | None, now: datetime) -> float | None:
    dt = parse_iso(value)
    if dt is None:
        return None
    return max(0.0, (now - dt).total_seconds() / 86400.0)


def new_id(prefix: str) -> str:
    """Random, collision-resistant identifier such as ``cyc_3f2a...``."""
    return f"{prefix}_{uuid.uuid4().hex}"


def dumps(value: Any) -> str:
    """Deterministic compact JSON used for persistence."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def loads(value: str | None, default: Any = None) -> Any:
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


def digest(value: Any) -> str:
    """Stable SHA-256 digest of a JSON-serialisable value (for idempotency and audit)."""
    return hashlib.sha256(dumps(value).encode("utf-8")).hexdigest()


def bounded(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(text: str) -> str:
    """Remove control characters (keeps newlines and tabs) and trim whitespace."""
    return _CONTROL.sub("", text).strip()


def normalise_key(text: str) -> str:
    """Canonical form for memory keys: lowercase, single spaces, no surrounding punctuation."""
    text = re.sub(r"[^\w\s\-.']", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip(" .'-")


PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


def make_private(path: Path | str, mode: int = PRIVATE_FILE_MODE) -> None:
    """Restrict a file or directory JARVIS created to its own OS user (best effort; a no-op
    where POSIX permissions do not apply). The default umask (022) would otherwise make
    personal data readable by every other account on a shared machine."""
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def file_mode(path: Path | str) -> str | None:
    try:
        return oct(os.stat(path).st_mode & 0o777)
    except OSError:
        return None
