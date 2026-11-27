"""Secret detection and redaction.

Every persistence and logging path (events, operation journal, provider errors,
API responses) passes text through :func:`redact` so that credentials are not
stored in the experience log or echoed in diagnostics.

Contract:
- ``redact(text)`` returns a :class:`Redaction` whose ``text`` has credential-like
  substrings replaced by ``[REDACTED:<kind>]`` and whose ``kinds`` lists what was found.
- Exact values registered with :func:`register_secret` (e.g. configured API keys)
  are always redacted, even if they do not match a known pattern.

Limitations (documented, tested): detection is pattern-based. It is deliberately
conservative (false positives such as "my secret is that ..." are accepted), and
it cannot recognise arbitrary private information that has no credential shape.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Any

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "private_key"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{35}"), "google_api_key"),
    (re.compile(r"\bsk-(?:proj-|ant-|live-|test-)?[A-Za-z0-9_\-]{20,}"), "api_key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), "github_token"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b"), "github_token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "aws_access_key"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b"), "slack_token"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"), "jwt"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-~+/]{16,}=*"), "bearer_token"),
)

# "password is hunter2", "api key: abc", "PIN=1234" -> keep the label, redact the value.
_LABELLED = re.compile(
    r"(?i)\b(password|passwd|passphrase|passcode|pin|otp|one[- ]time code|secret key|secret|"
    r"api[_ \-]?key|access[_ \-]?token|auth[_ \-]?token|token|private key)\b(\s*(?:is|=|:)\s*)([^\s,;]+)"
)

_registered: set[str] = set()
_lock = threading.Lock()


@dataclass(frozen=True)
class Redaction:
    text: str
    kinds: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.kinds)


def register_secret(value: str | None) -> None:
    """Register an exact secret value (e.g. a configured API key) for redaction.

    Values shorter than 8 characters are ignored to avoid redacting common words.
    """
    if value and len(value) >= 8:
        with _lock:
            _registered.add(value)


def redact(text: str) -> Redaction:
    if not text:
        return Redaction(text, ())
    kinds: list[str] = []
    with _lock:
        registered = sorted(_registered, key=len, reverse=True)
    for secret in registered:
        if secret in text:
            text = text.replace(secret, "[REDACTED:configured_secret]")
            kinds.append("configured_secret")
    for pattern, kind in _PATTERNS:
        text, n = pattern.subn(f"[REDACTED:{kind}]", text)
        if n:
            kinds.append(kind)

    def _labelled(match: re.Match[str]) -> str:
        if match.group(3).startswith("[REDACTED"):
            return match.group(0)
        kinds.append("credential")
        return f"{match.group(1)}{match.group(2)}[REDACTED:credential]"

    text = _LABELLED.sub(_labelled, text)
    return Redaction(text, tuple(dict.fromkeys(kinds)))


def redact_text(text: str) -> str:
    return redact(text).text


def contains_secret(text: str) -> bool:
    return redact(text).changed


def redact_obj(value: Any) -> Any:
    """Recursively redact strings inside dicts/lists/tuples (keys are preserved)."""
    if isinstance(value, str):
        return redact(value).text
    if isinstance(value, dict):
        return {k: redact_obj(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_obj(v) for v in value]
    return value
