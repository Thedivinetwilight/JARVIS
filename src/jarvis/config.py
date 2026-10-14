"""Small, dependency-free configuration loader."""
from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path

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

@dataclass(frozen=True)
class Settings:
    home: Path
    workspace: Path
    network_enabled: bool
    gemini_api_key: str
    gemini_model: str
    host: str
    port: int
    log_level: str

    @classmethod
    def load(cls, dotenv: Path | None = None) -> "Settings":
        _load_dotenv(dotenv or Path(".env"))
        home = Path(os.getenv("JARVIS_HOME", "~/.jarvis")).expanduser().resolve()
        workspace = Path(os.getenv("JARVIS_WORKSPACE", ".")).expanduser().resolve()
        return cls(
            home=home,
            workspace=workspace,
            network_enabled=_bool("JARVIS_NETWORK_ENABLED", False),
            gemini_api_key=os.getenv("JARVIS_GEMINI_API_KEY", ""),
            gemini_model=os.getenv("JARVIS_GEMINI_MODEL", "gemini-2.5-flash"),
            host=os.getenv("JARVIS_HOST", "127.0.0.1"),
            port=int(os.getenv("JARVIS_PORT", "8765")),
            log_level=os.getenv("JARVIS_LOG_LEVEL", "INFO").upper(),
        )
