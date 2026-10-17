"""Small, dependency-free configuration loader.

All settings come from environment variables (optionally from a local ``.env``).
Validation errors name the variable but never echo its value, and secret fields
are excluded from ``repr`` so settings can be logged safely.
See docs/CONFIGURATION.md for the full reference.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(ValueError):
    pass


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int, low: int, high: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer") from None
    if not low <= value <= high:
        raise ConfigError(f"{name} must be between {low} and {high}")
    return value


def _float(name: str, default: float, low: float, high: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number") from None
    if not low <= value <= high:
        raise ConfigError(f"{name} must be between {low} and {high}")
    return value


PROVIDERS = ("auto", "none", "gemini", "openai_compatible")


@dataclass(frozen=True)
class Settings:
    home: Path
    workspace: Path
    network_enabled: bool
    gemini_api_key: str = field(repr=False)
    gemini_model: str
    host: str
    port: int
    log_level: str
    # --- Genesis 0.1 additions (defaults keep v0.2 behaviour) ---
    provider: str = "auto"
    provider_timeout: float = 30.0
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    openai_base_url: str = ""
    openai_model: str = ""
    openai_api_key: str = field(default="", repr=False)
    autonomy_level: int = 2
    allow_sensitive_to_provider: bool = False
    affect_retention_days: int = 30
    session_idle_minutes: int = 30
    principal_name: str = "Sivaram"

    @classmethod
    def load(cls, dotenv: Path | None = None) -> "Settings":
        _load_dotenv(dotenv or Path(".env"))
        home = Path(os.getenv("JARVIS_HOME", "~/.jarvis")).expanduser().resolve()
        workspace = Path(os.getenv("JARVIS_WORKSPACE", ".")).expanduser().resolve()
        provider = os.getenv("JARVIS_PROVIDER", "auto").strip().lower()
        if provider not in PROVIDERS:
            raise ConfigError(f"JARVIS_PROVIDER must be one of {', '.join(PROVIDERS)}")
        autonomy = _int("JARVIS_AUTONOMY_LEVEL", 2, 0, 5)
        if autonomy > 3:
            raise ConfigError("JARVIS_AUTONOMY_LEVEL 4-5 are designed but not implemented in this build")
        return cls(
            home=home,
            workspace=workspace,
            network_enabled=_bool("JARVIS_NETWORK_ENABLED", False),
            gemini_api_key=os.getenv("JARVIS_GEMINI_API_KEY", ""),
            gemini_model=os.getenv("JARVIS_GEMINI_MODEL", "gemini-2.5-flash"),
            host=os.getenv("JARVIS_HOST", "127.0.0.1"),
            port=_int("JARVIS_PORT", 8765, 1, 65535),
            log_level=os.getenv("JARVIS_LOG_LEVEL", "INFO").upper(),
            provider=provider,
            provider_timeout=_float("JARVIS_PROVIDER_TIMEOUT", 30.0, 0.1, 600.0),
            gemini_base_url=os.getenv("JARVIS_GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"),
            openai_base_url=os.getenv("JARVIS_OPENAI_BASE_URL", "").strip(),
            openai_model=os.getenv("JARVIS_OPENAI_MODEL", "").strip(),
            openai_api_key=os.getenv("JARVIS_OPENAI_API_KEY", ""),
            autonomy_level=autonomy,
            allow_sensitive_to_provider=_bool("JARVIS_ALLOW_SENSITIVE_TO_PROVIDER", False),
            affect_retention_days=_int("JARVIS_AFFECT_RETENTION_DAYS", 30, 0, 3650),
            session_idle_minutes=_int("JARVIS_SESSION_IDLE_MINUTES", 30, 1, 10080),
            principal_name=os.getenv("JARVIS_PRINCIPAL_NAME", "Sivaram").strip() or "Sivaram",
        )

    def describe(self) -> dict[str, object]:
        """Configuration summary that is safe to print (secrets reported as set/unset only)."""
        return {"home": str(self.home), "workspace": str(self.workspace), "network_enabled": self.network_enabled,
                "provider": self.provider, "gemini_model": self.gemini_model,
                "gemini_api_key": "set" if self.gemini_api_key else "unset",
                "openai_base_url": self.openai_base_url or None, "openai_model": self.openai_model or None,
                "openai_api_key": "set" if self.openai_api_key else "unset",
                "autonomy_level": self.autonomy_level, "allow_sensitive_to_provider": self.allow_sensitive_to_provider,
                "affect_retention_days": self.affect_retention_days,
                "session_idle_minutes": self.session_idle_minutes, "host": self.host, "port": self.port}
