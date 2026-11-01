"""Companion configuration: one server, one cadence, no secrets.

The configuration file holds **no token**. The device credential lives in the OS-protected store
(Windows Credential Manager); the file only names the server, the cadence and this client's identity,
because a token on disk in plaintext would make the pairing pointless.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit

from . import PLATFORM_LABEL
from .errors import ConfigInvalid, InsecureTransport

#: The owner's surfacing target is 10 seconds while online. The companion will not accept a slower
#: cadence: a setting above this would silently make the target unreachable by construction.
MAX_POLL_SECONDS = 10
MIN_POLL_SECONDS = 1

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def default_state_dir() -> Path:
    """Where configuration, the journal and logs live.

    Windows: ``%LOCALAPPDATA%\\JARVIS\\Companion``. Elsewhere (tests, and the honest "this build is the
    Windows companion" message): ``$XDG_STATE_HOME/jarvis-companion`` or ``~/.local/state/...``.
    """
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "JARVIS" / "Companion"
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "jarvis-companion"


@dataclass(frozen=True)
class CompanionConfig:
    server_url: str
    poll_seconds: int = MAX_POLL_SECONDS
    label: str = "Windows PC"
    client_id: str | None = None
    allow_insecure_http: bool = False

    def __post_init__(self) -> None:
        validate_server_url(self.server_url, allow_insecure_http=self.allow_insecure_http)
        if not MIN_POLL_SECONDS <= int(self.poll_seconds) <= MAX_POLL_SECONDS:
            raise ConfigInvalid(
                f"poll_seconds must be between {MIN_POLL_SECONDS} and {MAX_POLL_SECONDS}; "
                f"{self.poll_seconds} would miss the owner's 10-second surfacing target by design",
                detail={"poll_seconds": self.poll_seconds},
            )
        if not str(self.label).strip():
            raise ConfigInvalid("label must not be empty")

    # ------------------------------------------------------------------ persistence
    def to_dict(self) -> dict:
        return {
            "server_url": self.server_url,
            "poll_seconds": int(self.poll_seconds),
            "label": self.label,
            "client_id": self.client_id,
            "allow_insecure_http": bool(self.allow_insecure_http),
        }

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        try:  # the file names a server and a cadence; keep it owner-only where the OS supports it
            path.chmod(0o600)
        except OSError:  # pragma: no cover - Windows ACLs, not POSIX modes
            pass
        return path

    @classmethod
    def load(cls, path: Path) -> "CompanionConfig":
        if not path.is_file():
            raise ConfigInvalid(
                f"no companion configuration at {path}; run `jarvis-companion pair ...` first",
                detail={"path": str(path)},
            )
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise ConfigInvalid(f"configuration at {path} is not readable JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigInvalid(f"configuration at {path} must be a JSON object")
        unknown = set(raw) - {"server_url", "poll_seconds", "label", "client_id",
                              "allow_insecure_http"}
        if unknown:
            raise ConfigInvalid(
                f"unknown configuration keys: {', '.join(sorted(unknown))}",
                detail={"unknown": sorted(unknown)},
            )
        if "server_url" not in raw:
            raise ConfigInvalid("configuration has no server_url")
        return cls(
            server_url=str(raw["server_url"]),
            poll_seconds=int(raw.get("poll_seconds", MAX_POLL_SECONDS)),
            label=str(raw.get("label", "Windows PC")),
            client_id=raw.get("client_id"),
            allow_insecure_http=bool(raw.get("allow_insecure_http", False)),
        )

    def with_client(self, client_id: str, *, label: str | None = None) -> "CompanionConfig":
        return replace(self, client_id=client_id, label=label or self.label)

    def with_poll_seconds(self, poll_seconds: int) -> "CompanionConfig":
        return replace(self, poll_seconds=int(poll_seconds))

    def with_client_cleared(self) -> "CompanionConfig":
        """Forget the paired identity after a revoke or a local forget."""
        return replace(self, client_id=None)

    # ------------------------------------------------------------------ derived
    @property
    def host(self) -> str:
        return urlsplit(self.server_url).hostname or ""

    @property
    def port(self) -> int:
        parts = urlsplit(self.server_url)
        if parts.port:
            return parts.port
        return 443 if parts.scheme == "https" else 80

    @property
    def base(self) -> str:
        return self.server_url.rstrip("/")


def validate_server_url(url: str, *, allow_insecure_http: bool = False) -> None:
    """Only http(s), only a host, and no plaintext credentials to a remote machine.

    The device token is a bearer credential: over plaintext HTTP to anything but this machine it is
    readable by every hop. Loopback is allowed because the packet never leaves the host.
    """
    parts = urlsplit(url or "")
    if parts.scheme not in ("http", "https"):
        raise ConfigInvalid(
            f"server_url must start with http:// or https://, not {url!r}",
            detail={"server_url": url},
        )
    if not parts.hostname:
        raise ConfigInvalid(f"server_url has no host: {url!r}", detail={"server_url": url})
    if parts.query or parts.fragment:
        raise ConfigInvalid("server_url must not carry a query or fragment")
    if parts.path not in ("", "/"):
        raise ConfigInvalid(
            f"server_url must be an origin (scheme://host:port), not an endpoint: {url!r} carries "
            f"the path {parts.path!r}, and the companion appends its own API paths",
            detail={"path": parts.path},
        )
    if parts.scheme == "http" and parts.hostname.lower() not in LOOPBACK_HOSTS:
        if not allow_insecure_http:
            raise InsecureTransport(
                "refusing plaintext HTTP to a machine other than this one: the device token would "
                "travel in the clear. Use https:// (the server supports TLS) or run the server on "
                "this PC, or pass --allow-insecure-http if the link is already trusted",
                detail={"server_url": url, "host": parts.hostname},
            )


__all__ = [
    "CompanionConfig",
    "LOOPBACK_HOSTS",
    "MAX_POLL_SECONDS",
    "MIN_POLL_SECONDS",
    "PLATFORM_LABEL",
    "default_state_dir",
    "validate_server_url",
]
