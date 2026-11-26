"""Structured logging for JARVIS.

JSON-lines log records with secret redaction, level filtering, and optional
file output. Every log line is a single JSON object with timestamp, level,
logger name, message, and structured fields. Secrets are redacted before
writing. Standard library only.

Usage:
    from jarvis.logging import get_logger, info, error
    log = get_logger("jarvis.kernel")
    info(log, "cycle started", cycle_id=cycle_id, intent=intent)
    error(log, "tool failed", capability=name, error_type=type(exc).__name__)

The helpers attach fields through ``extra=`` because the standard library
``Logger`` does not accept arbitrary keyword arguments.
"""
from __future__ import annotations

import json
import logging
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .redact import redact_text

# Log levels
DEBUG = logging.DEBUG
INFO = logging.INFO
WARNING = logging.WARNING
ERROR = logging.ERROR
CRITICAL = logging.CRITICAL

_LEVEL_NAMES = {DEBUG: "DEBUG", INFO: "INFO", WARNING: "WARNING", ERROR: "ERROR", CRITICAL: "CRITICAL"}

_lock = threading.Lock()
_handlers: list[logging.Handler] = []
_configured = False


class _JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON with redacted fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": _LEVEL_NAMES.get(record.levelno, str(record.levelno)),
            "logger": record.name,
            "message": redact_text(record.getMessage()),
        }
        # Structured fields passed via extra=
        for key in ("cycle_id", "session_id", "capability", "operation_id", "intent",
                    "provider", "error_type", "duration_ms", "status", "reason"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = redact_text(value) if isinstance(value, str) else value
        if record.exc_info:
            payload["exception"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class _RedactingFilter(logging.Filter):
    """Ensure no secret leaks through any log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.msg:
            record.msg = redact_text(str(record.msg))
        if record.args:
            record.args = tuple(redact_text(str(a)) if isinstance(a, str) else a for a in record.args)
        return True


def configure_logging(level: str = "INFO", log_file: Path | None = None, *,
                      to_stderr: bool = True) -> None:
    """Configure root JARVIS logging. Idempotent; safe to call multiple times."""
    global _configured
    numeric = getattr(logging, level.upper(), logging.INFO)
    root = logging.getLogger("jarvis")
    root.setLevel(numeric)

    with _lock:
        # Remove existing handlers we added
        for h in list(root.handlers):
            if getattr(h, "_jarvis", False):
                root.removeHandler(h)
                try:
                    h.close()
                except Exception:
                    pass
        _handlers.clear()

        if to_stderr:
            handler = logging.StreamHandler(sys.stderr)
            handler.setFormatter(_JsonFormatter())
            handler.addFilter(_RedactingFilter())
            handler._jarvis = True  # type: ignore[attr-defined]
            root.addHandler(handler)
            _handlers.append(handler)

        if log_file is not None:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(str(log_file), encoding="utf-8")
            fh.setFormatter(_JsonFormatter())
            fh.addFilter(_RedactingFilter())
            fh._jarvis = True  # type: ignore[attr-defined]
            root.addHandler(fh)
            _handlers.append(fh)

        # Prevent propagation to root (avoid duplicate non-JSON lines)
        root.propagate = False
        _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a JARVIS logger. Auto-configures with INFO to stderr on first use."""
    if not _configured:
        configure_logging()
    return logging.getLogger(name if name.startswith("jarvis") else f"jarvis.{name}")


def log_event(logger: logging.Logger, level: int, message: str, **fields: Any) -> None:
    """Log with structured fields. Fields are attached as record attributes."""
    logger.log(level, message, extra=fields)


def info(logger: logging.Logger, message: str, **fields: Any) -> None:
    log_event(logger, INFO, message, **fields)


def warning(logger: logging.Logger, message: str, **fields: Any) -> None:
    log_event(logger, WARNING, message, **fields)


def error(logger: logging.Logger, message: str, **fields: Any) -> None:
    log_event(logger, ERROR, message, **fields)


def debug(logger: logging.Logger, message: str, **fields: Any) -> None:
    log_event(logger, DEBUG, message, **fields)
