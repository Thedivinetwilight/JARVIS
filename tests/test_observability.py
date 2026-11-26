"""Structured logging: JSON lines, redaction, level filtering, no secret leakage."""
from __future__ import annotations

import json

from jarvis.logging import configure_logging, error, get_logger, info
from jarvis.redact import register_secret


def _records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_records_are_one_json_object_per_line(tmp_path):
    log_file = tmp_path / "logs" / "jarvis.log"
    configure_logging("INFO", log_file=log_file, to_stderr=False)
    log = get_logger("jarvis.test")
    info(log, "cycle started", cycle_id="cyc_1", intent="remember")
    error(log, "tool failed", capability="memory.save", error_type="ValueError")
    records = _records(log_file)
    assert records[0]["level"] == "INFO" and records[0]["logger"] == "jarvis.test"
    assert records[0]["message"] == "cycle started"
    assert records[0]["cycle_id"] == "cyc_1" and records[0]["intent"] == "remember"
    assert records[1]["level"] == "ERROR" and records[1]["capability"] == "memory.save"
    assert all("ts" in r and "level" in r and "message" in r for r in records)


def test_level_filtering_drops_lower_levels(tmp_path):
    log_file = tmp_path / "jarvis.log"
    configure_logging("WARNING", log_file=log_file, to_stderr=False)
    log = get_logger("jarvis.filter")
    info(log, "dropped")
    error(log, "kept")
    messages = [r["message"] for r in _records(log_file)]
    assert messages == ["kept"]


def test_registered_secrets_are_redacted_everywhere(tmp_path):
    secret = "sk-live-" + "A1B2C3D4E5F6G7H8"
    register_secret(secret)
    log_file = tmp_path / "jarvis.log"
    configure_logging("INFO", log_file=log_file, to_stderr=False)
    log = get_logger("jarvis.redact")
    info(log, f"calling provider with key {secret}")
    info(log, "key in a field", reason=f"token={secret}")
    raw = log_file.read_text(encoding="utf-8")
    assert secret not in raw
    assert all(secret not in r["message"] for r in _records(log_file))


def test_exceptions_are_rendered_and_redacted(tmp_path):
    secret = "AIza" + "VerySecretKeyValue0123456789"
    register_secret(secret)
    log_file = tmp_path / "jarvis.log"
    configure_logging("INFO", log_file=log_file, to_stderr=False)
    log = get_logger("jarvis.exc")
    try:
        raise RuntimeError(f"upstream rejected {secret}")
    except RuntimeError:
        log.exception("provider error")
    raw = log_file.read_text(encoding="utf-8")
    assert secret not in raw
    assert "RuntimeError" in raw


def test_configuration_is_idempotent_and_does_not_stack_handlers():
    import logging
    root = logging.getLogger("jarvis")
    configure_logging("INFO", to_stderr=False)
    first = len(root.handlers)
    configure_logging("DEBUG", to_stderr=False)
    assert len(root.handlers) == first


def test_helpers_cannot_be_called_with_raw_kwargs_on_stdlib_logger():
    """The bug this guards: stdlib Logger rejects structlog-style kwargs."""
    import pytest
    log = get_logger("jarvis.guard")
    with pytest.raises(TypeError):
        log.info("JARVIS started", runtime_id="x")     # documented failure mode
    info(log, "JARVIS started", runtime_id="x")        # the supported helper
