"""Token minting, secret storage and log redaction.

Tokens and pairing codes are shown once, stored only as HMAC-SHA256 digests under the
server secret, and compared in constant time. Nothing in this module writes a secret to
SQLite, to a log line or to an audit record; ``redact`` is applied to every free-text
value that could carry one.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets as _secrets
import re
from typing import Any

TOKEN_PREFIX = "jvs"
PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no I/O/0/1: pairing codes get typed
PAIRING_GROUP = 4
PAIRING_GROUPS = 3

REDACTED = "<redacted>"

# NB: a bare "code" key is *not* sensitive here: audit details carry error codes such as
# "pairing_code_invalid", and redacting those would make the trail useless.
_SENSITIVE_KEYS = {
    "token", "access_token", "authorization", "secret", "server_secret", "password",
    "pairing_code", "code_hash", "auth_code", "credential", "signature",
}

_TOKEN_PATTERN = re.compile(r"jvs_[A-Za-z0-9]{4,}\.[A-Za-z0-9_\-]{8,}")
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+")
_PAIRING_PATTERN = re.compile(r"\b[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}\b")


def digest(secret: str, value: str) -> str:
    """HMAC-SHA256 of ``value`` under the server secret, hex encoded."""
    return hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def mint_token(server_secret: str, client_id: str) -> tuple[str, str, str]:
    """Return ``(token, digest, prefix)``; the token is returned to the owner exactly once."""
    body = _secrets.token_urlsafe(32)
    token = f"{TOKEN_PREFIX}_{client_id}.{body}"
    # The prefix is the start of the random body, not of the constant envelope, so an
    # operator can tell two paired clients apart in a listing without seeing the token.
    return token, digest(server_secret, token), token.rsplit(".", 1)[-1][:8]


def mint_pairing_code() -> str:
    groups = []
    for _ in range(PAIRING_GROUPS):
        groups.append(
            "".join(_secrets.choice(PAIRING_ALPHABET) for _ in range(PAIRING_GROUP))
        )
    return "-".join(groups)


def normalise_pairing_code(raw: str) -> str:
    return re.sub(r"[^A-Z2-9]", "", (raw or "").upper())


def redact(value: str) -> str:
    """Mask anything that looks like a token, a bearer header or a pairing code."""
    text = _BEARER_PATTERN.sub("Bearer " + REDACTED, value)
    text = _TOKEN_PATTERN.sub(f"{TOKEN_PREFIX}_" + REDACTED, text)
    text = _PAIRING_PATTERN.sub(REDACTED, text)
    return text


def scrub(value: Any) -> Any:
    """Recursively redact a JSON-like structure before it is logged or audited."""
    if isinstance(value, dict):
        scrubbed = {}
        for key, item in value.items():
            if str(key).lower() in _SENSITIVE_KEYS:
                scrubbed[key] = REDACTED
            else:
                scrubbed[key] = scrub(item)
        return scrubbed
    if isinstance(value, (list, tuple)):
        return [scrub(item) for item in value]
    if isinstance(value, str):
        return redact(value)
    return value


def contains_secret(text: str, *candidates: str) -> bool:
    """True if any candidate secret appears verbatim in ``text`` (used by tests)."""
    return any(candidate and candidate in text for candidate in candidates)
