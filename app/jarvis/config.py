"""Explicit configuration and startup validation.

Rules that are enforced here, and tested in ``app/tests/test_config.py``:

* unknown sections or keys are refused (a typo must not silently become a default);
* the server secret is never read from the config file, only from
  ``JARVIS_SERVER_SECRET`` or a 0600 secret file, and never appears in a log line;
* a non-loopback bind requires TLS material;
* every declared external service is refused while its adapter does not exist;
* every speech adapter must be ``unavailable``: no model is selected (gate G-O1 is
  blocked), so the server must not pretend that speech works;
* the data directory is created private (0700) and must be writable, and it may not sit
  inside the evaluator or the documentation trees.
"""

from __future__ import annotations

import os
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ConfigError, StartupRefused

REPO_ROOT = Path(__file__).resolve().parents[2]

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
LOG_LEVELS = {"debug", "info", "warning", "error"}
PLACEHOLDER_SECRETS = {
    "changeme", "change-me", "secret", "password", "replace-me", "replaceme",
    "dev", "development", "test", "example", "please-change-this",
}

#: section -> key -> expected type, and whether the key is required.
SCHEMA: dict[str, dict[str, tuple[type, bool]]] = {
    "server": {
        "bind": (str, True),
        "port": (int, True),
        "tls_cert": (str, False),
        "tls_key": (str, False),
        "request_timeout_s": (int, False),
        "max_request_bytes": (int, False),
    },
    "storage": {
        "data_dir": (str, True),
        "database": (str, False),
        "audio_retention_days": (int, False),
        "max_audio_bytes_per_session": (int, False),
        "max_audio_chunk_bytes": (int, False),
        "audio_session_ttl_minutes": (int, False),
    },
    "auth": {
        "device_token_ttl_days": (int, False),
        "operator_token_ttl_days": (int, False),
        "pairing_code_ttl_minutes": (int, False),
        "pairing_max_failed_attempts": (int, False),
        "secret_file": (str, False),
    },
    "external_services": {
        "web_retrieval": (bool, False),
        "telephony": (bool, False),
        "device_vendor_cloud": (bool, False),
        "update_downloads": (bool, False),
    },
    "adapters": {
        "speech_asr": (str, False),
        "speech_tts": (str, False),
    },
    "reminders": {
        "display_timezone": (str, False),
        "max_delivery_attempts": (int, False),
        "retry_interval_minutes": (int, False),
        "delivery_window_minutes": (int, False),
    },
    "logging": {
        "level": (str, False),
    },
}

DEFAULTS: dict[str, dict[str, object]] = {
    "server": {
        "tls_cert": "",
        "tls_key": "",
        "request_timeout_s": 30,
        "max_request_bytes": 1_048_576,
    },
    "storage": {
        "database": "jarvis.sqlite3",
        "audio_retention_days": 7,
        "max_audio_bytes_per_session": 20_000_000,
        "max_audio_chunk_bytes": 1_048_576,
        "audio_session_ttl_minutes": 15,
    },
    "auth": {
        "device_token_ttl_days": 90,
        "operator_token_ttl_days": 30,
        "pairing_code_ttl_minutes": 10,
        "pairing_max_failed_attempts": 5,
        "secret_file": "",
    },
    "external_services": {
        "web_retrieval": False,
        "telephony": False,
        "device_vendor_cloud": False,
        "update_downloads": False,
    },
    "adapters": {
        "speech_asr": "unavailable",
        "speech_tts": "unavailable",
    },
    # Reminder delivery is a local queue that a paired client polls. These are the only knobs:
    # a time zone for rendering (storage is always UTC), a retry limit, the minimum gap between
    # hand-overs that counts as a new attempt, and how long an unacknowledged reminder stays
    # deliverable before it is marked expired.
    "reminders": {
        "display_timezone": "UTC",
        "max_delivery_attempts": 3,
        "retry_interval_minutes": 5,
        "delivery_window_minutes": 1440,
    },
    "logging": {"level": "info"},
}

MIN_SECRET_BYTES = 32


@dataclass(frozen=True)
class ServerConfig:
    bind: str
    port: int
    tls_cert: str
    tls_key: str
    request_timeout_s: int
    max_request_bytes: int

    @property
    def is_loopback(self) -> bool:
        return self.bind in LOOPBACK_HOSTS

    @property
    def tls_enabled(self) -> bool:
        return bool(self.tls_cert and self.tls_key)


@dataclass(frozen=True)
class StorageConfig:
    data_dir: Path
    database: str
    audio_retention_days: int
    max_audio_bytes_per_session: int
    max_audio_chunk_bytes: int
    audio_session_ttl_minutes: int

    @property
    def database_path(self) -> Path:
        return self.data_dir / self.database

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"


@dataclass(frozen=True)
class AuthConfig:
    device_token_ttl_days: int
    operator_token_ttl_days: int
    pairing_code_ttl_minutes: int
    pairing_max_failed_attempts: int
    secret_file: str


@dataclass(frozen=True)
class RemindersConfig:
    """Delivery policy. Every value is explicit; none of them is inferred from a device."""

    display_timezone: str
    max_delivery_attempts: int
    retry_interval_minutes: int
    delivery_window_minutes: int


@dataclass(frozen=True)
class Config:
    path: Path
    server: ServerConfig
    storage: StorageConfig
    auth: AuthConfig
    external_services: dict[str, bool]
    adapters: dict[str, str]
    reminders: RemindersConfig
    logging_level: str
    #: Never logged, never serialised, never written to the data directory.
    server_secret: str = field(repr=False, default="")

    def public_dict(self) -> dict:
        """Config view that is safe to log and to serve from ``/v1/status``."""
        return {
            "server": {
                "bind": self.server.bind,
                "port": self.server.port,
                "tls": self.server.tls_enabled,
                "max_request_bytes": self.server.max_request_bytes,
            },
            "storage": {
                "data_dir": str(self.storage.data_dir),
                "database": self.storage.database,
                "audio_retention_days": self.storage.audio_retention_days,
                "max_audio_bytes_per_session": self.storage.max_audio_bytes_per_session,
            },
            "auth": {
                "device_token_ttl_days": self.auth.device_token_ttl_days,
                "operator_token_ttl_days": self.auth.operator_token_ttl_days,
                "pairing_code_ttl_minutes": self.auth.pairing_code_ttl_minutes,
                "pairing_max_failed_attempts": self.auth.pairing_max_failed_attempts,
            },
            "external_services": dict(self.external_services),
            "adapters": dict(self.adapters),
            "reminders": {
                "display_timezone": self.reminders.display_timezone,
                "max_delivery_attempts": self.reminders.max_delivery_attempts,
                "retry_interval_minutes": self.reminders.retry_interval_minutes,
                "delivery_window_minutes": self.reminders.delivery_window_minutes,
                "delivery_model": "local queue, retrieved by a paired client; no push, no speech",
            },
        }


def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"config file is not valid TOML: {exc}") from exc


def _check_shape(raw: dict) -> None:
    problems: list[str] = []
    for section, value in raw.items():
        if section not in SCHEMA:
            problems.append(f"unknown section [{section}]")
            continue
        if not isinstance(value, dict):
            problems.append(f"[{section}] must be a table")
            continue
        for key, item in value.items():
            if key not in SCHEMA[section]:
                problems.append(f"unknown key {section}.{key}")
                continue
            expected, _required = SCHEMA[section][key]
            if not isinstance(item, expected):
                problems.append(
                    f"{section}.{key} must be {expected.__name__}, got {type(item).__name__}"
                )
    if problems:
        raise ConfigError("invalid configuration: " + "; ".join(sorted(problems)))


def _require(raw: dict, section: str) -> None:
    missing = [
        f"{section}.{key}"
        for key, (_type, required) in SCHEMA[section].items()
        if required and key not in raw.get(section, {})
    ]
    if missing:
        raise ConfigError("missing required configuration: " + ", ".join(sorted(missing)))


def _secret_from_file(path: Path) -> str:
    if not path.is_file():
        raise ConfigError(f"auth.secret_file does not exist: {path}")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise ConfigError(
            f"auth.secret_file must not be readable by group or others (mode {oct(mode)})"
        )
    return path.read_text(encoding="utf-8").strip()


def _resolve_secret(raw: dict, base: Path, environ: dict) -> str:
    from_env = (environ.get("JARVIS_SERVER_SECRET") or "").strip()
    if from_env:
        return from_env
    secret_file = raw.get("auth", {}).get("secret_file", "")
    if secret_file:
        candidate = Path(secret_file)
        if not candidate.is_absolute():
            candidate = base / candidate
        return _secret_from_file(candidate)
    raise ConfigError(
        "no server secret: set JARVIS_SERVER_SECRET (>=32 bytes) or auth.secret_file "
        "pointing at a 0600 file"
    )


def _validate_secret(secret: str) -> None:
    if len(secret.encode("utf-8")) < MIN_SECRET_BYTES:
        raise ConfigError(
            f"server secret must be at least {MIN_SECRET_BYTES} bytes "
            f"(got {len(secret.encode('utf-8'))})"
        )
    lowered = secret.strip().lower()
    looks_like_placeholder = lowered in PLACEHOLDER_SECRETS or any(
        lowered.startswith(marker + separator)
        for marker in PLACEHOLDER_SECRETS
        for separator in ("-", "_", " ", ".", ":")
    )
    if looks_like_placeholder:
        raise ConfigError("server secret is a placeholder value; generate a real one")
    if len(set(secret)) < 8:
        raise ConfigError("server secret has too little variety; generate a random one")


def _prepare_data_dir(configured: str, base: Path) -> Path:
    candidate = Path(configured)
    if not candidate.is_absolute():
        candidate = base / candidate
    resolved = candidate.resolve()
    forbidden = ((REPO_ROOT / "eval").resolve(), (REPO_ROOT / "docs").resolve())
    for blocked in forbidden:
        if resolved == blocked or blocked in resolved.parents:
            raise ConfigError(
                f"storage.data_dir must not live inside {blocked.name}/ (evaluation and "
                "documentation trees are never runtime state)"
            )
    try:
        resolved.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise ConfigError(f"storage.data_dir cannot be created: {exc}") from exc
    if not resolved.is_dir():
        raise ConfigError(f"storage.data_dir is not a directory: {resolved}")
    # The audio root holds raw voice recordings: it is created private here so that a
    # session directory's parents cannot be world-readable.
    audio_root = resolved / "audio"
    try:
        audio_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        audio_root.chmod(0o700)
    except OSError as exc:
        raise ConfigError(f"storage.audio directory cannot be prepared: {exc}") from exc
    try:
        probe = resolved / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise ConfigError(f"storage.data_dir is not writable: {exc}") from exc
    return resolved


def _validate_timezone(name: str) -> None:
    """A display time zone must be a real IANA zone, resolved locally, with no network call.

    ``UTC`` is accepted because it is what the storage layer already uses; an empty or invented
    name is refused rather than defaulted, so the owner cannot silently get the wrong wall clock
    on a daylight-saving boundary.
    """
    import zoneinfo

    if not name:
        raise ConfigError("reminders.display_timezone must not be empty")
    try:
        zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(
            f"reminders.display_timezone is not a known IANA time zone: {name!r}"
        ) from exc


def load_config(path: str | Path, *, environ: dict | None = None) -> Config:
    """Load, shape-check and validate a configuration file.

    Raises ``ConfigError`` for anything structurally wrong and ``StartupRefused`` for a
    configuration that is well-formed but refuses to run (an external service switched
    on, or a speech adapter other than ``unavailable``).
    """
    environ = dict(os.environ if environ is None else environ)
    config_path = Path(path)
    base = config_path.resolve().parent
    raw = _read_toml(config_path)
    _check_shape(raw)
    for section in SCHEMA:
        _require(raw, section)

    merged: dict[str, dict[str, object]] = {}
    for section, keys in SCHEMA.items():
        merged[section] = dict(DEFAULTS.get(section, {}))
        merged[section].update(raw.get(section, {}))

    secret = _resolve_secret(raw, base, environ)
    _validate_secret(secret)

    server = ServerConfig(
        bind=str(merged["server"]["bind"]),
        port=int(merged["server"]["port"]),
        tls_cert=str(merged["server"]["tls_cert"]),
        tls_key=str(merged["server"]["tls_key"]),
        request_timeout_s=int(merged["server"]["request_timeout_s"]),
        max_request_bytes=int(merged["server"]["max_request_bytes"]),
    )
    if not 1 <= server.port <= 65535:
        raise ConfigError("server.port must be between 1 and 65535")
    if server.request_timeout_s < 1:
        raise ConfigError("server.request_timeout_s must be positive")
    if server.max_request_bytes < 1024:
        raise ConfigError("server.max_request_bytes must be at least 1024")
    if not server.is_loopback and not server.tls_enabled:
        raise ConfigError(
            "refusing to bind a non-loopback address without TLS: set server.tls_cert and "
            "server.tls_key, or bind 127.0.0.1 behind a local reverse proxy"
        )
    if bool(server.tls_cert) != bool(server.tls_key):
        raise ConfigError("server.tls_cert and server.tls_key must be set together")
    if server.tls_enabled:
        for label, value in (("server.tls_cert", server.tls_cert), ("server.tls_key", server.tls_key)):
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = base / candidate
            if not candidate.is_file():
                raise ConfigError(f"{label} does not exist: {candidate}")

    data_dir = _prepare_data_dir(str(merged["storage"]["data_dir"]), base)
    storage = StorageConfig(
        data_dir=data_dir,
        database=str(merged["storage"]["database"]),
        audio_retention_days=int(merged["storage"]["audio_retention_days"]),
        max_audio_bytes_per_session=int(merged["storage"]["max_audio_bytes_per_session"]),
        max_audio_chunk_bytes=int(merged["storage"]["max_audio_chunk_bytes"]),
        audio_session_ttl_minutes=int(merged["storage"]["audio_session_ttl_minutes"]),
    )
    if storage.audio_retention_days < 0:
        raise ConfigError("storage.audio_retention_days must not be negative")
    if storage.max_audio_chunk_bytes < 1 or storage.max_audio_bytes_per_session < 1:
        raise ConfigError("audio byte limits must be positive")
    if storage.max_audio_chunk_bytes > storage.max_audio_bytes_per_session:
        raise ConfigError(
            "storage.max_audio_chunk_bytes must not exceed max_audio_bytes_per_session"
        )
    if storage.audio_session_ttl_minutes < 1:
        raise ConfigError("storage.audio_session_ttl_minutes must be positive")
    if Path(storage.database).name != storage.database:
        raise ConfigError("storage.database must be a file name, not a path")

    auth = AuthConfig(**{key: merged["auth"][key] for key in (
        "device_token_ttl_days", "operator_token_ttl_days", "pairing_code_ttl_minutes",
        "pairing_max_failed_attempts", "secret_file",
    )})
    for label, value in (
        ("auth.device_token_ttl_days", auth.device_token_ttl_days),
        ("auth.operator_token_ttl_days", auth.operator_token_ttl_days),
        ("auth.pairing_code_ttl_minutes", auth.pairing_code_ttl_minutes),
    ):
        if not 1 <= int(value) <= 365 * 24 * 60:
            raise ConfigError(f"{label} is out of range")
    if not 1 <= auth.pairing_max_failed_attempts <= 100:
        raise ConfigError("auth.pairing_max_failed_attempts must be between 1 and 100")

    enabled = {name: bool(flag) for name, flag in merged["external_services"].items()}
    if any(enabled.values()):
        switched_on = ", ".join(sorted(name for name, flag in enabled.items() if flag))
        raise StartupRefused(
            "external services are declared but no adapter can serve them, and none is "
            f"approved for use yet: {switched_on}. Leave them false until each one is "
            "individually scoped and approved (docs/product/capability-specification.md 0.2)."
        )

    adapters = {name: str(value) for name, value in merged["adapters"].items()}
    unusable = {name: value for name, value in adapters.items() if value != "unavailable"}
    if unusable:
        raise StartupRefused(
            "no model or speech component is selected, so every speech adapter must be "
            f"'unavailable' (got {unusable}). Selecting one is gate G-O1 and is blocked."
        )

    reminders = RemindersConfig(
        display_timezone=str(merged["reminders"]["display_timezone"]).strip(),
        max_delivery_attempts=int(merged["reminders"]["max_delivery_attempts"]),
        retry_interval_minutes=int(merged["reminders"]["retry_interval_minutes"]),
        delivery_window_minutes=int(merged["reminders"]["delivery_window_minutes"]),
    )
    _validate_timezone(reminders.display_timezone)
    if not 1 <= reminders.max_delivery_attempts <= 10:
        raise ConfigError("reminders.max_delivery_attempts must be between 1 and 10")
    if not 1 <= reminders.retry_interval_minutes <= 1440:
        raise ConfigError("reminders.retry_interval_minutes must be between 1 and 1440")
    if not 1 <= reminders.delivery_window_minutes <= 43200:
        raise ConfigError("reminders.delivery_window_minutes must be between 1 and 43200")

    level = str(merged["logging"]["level"]).lower()
    if level not in LOG_LEVELS:
        raise ConfigError(f"logging.level must be one of {sorted(LOG_LEVELS)}")

    return Config(
        path=config_path,
        server=server,
        storage=storage,
        auth=auth,
        external_services=enabled,
        adapters=adapters,
        reminders=reminders,
        logging_level=level,
        server_secret=secret,
    )
