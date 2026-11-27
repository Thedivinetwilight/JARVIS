"""Secret redaction across logs, settings and persisted conversation."""
from __future__ import annotations

from conftest import FAKE_GEMINI_KEY, db_bytes
from jarvis.redact import contains_secret, redact, register_secret


def test_patterns():
    samples = {
        "key AIza" + "SyA1234567890abcdefghijklmnopqrstuv here": "google_api_key",
        "sk-proj-abcdefghijklmnopqrstuvwxyz123456": "api_key",
        "ghp_" + "x" * 36: "github_token",
        "AKIA" + "ABCDEFGHIJKLMNOP": "aws_access_key",
        "Authorization: Bearer abcdefghijklmnop1234567890": "bearer_token",
        "my password is hunter2": "credential",
        "PIN=1234": "credential",
    }
    for text, kind in samples.items():
        result = redact(text)
        assert kind in result.kinds, text
        assert "[REDACTED" in result.text
    assert not contains_secret("the pump draws 2A at 12V")


def test_registered_secret_is_redacted_even_without_pattern():
    register_secret("correct-horse-battery-staple")
    assert "correct-horse" not in redact("the phrase is correct-horse-battery-staple ok").text


def test_settings_repr_hides_secrets(settings_factory):
    settings = settings_factory(gemini_api_key=FAKE_GEMINI_KEY, openai_api_key="sk-local-abcdefghijklmnopqrstuv")
    assert FAKE_GEMINI_KEY not in repr(settings) and "sk-local" not in repr(settings)
    assert settings.describe()["gemini_api_key"] == "set"


def test_secret_typed_in_conversation_is_never_persisted(app_factory):
    app = app_factory()
    secret = "ghp_" + "Q" * 36
    result = app.ask(f"remember that my github token is {secret}")
    assert secret not in result["answer"]
    assert any("redacted" in note.lower() for note in result["notes"])
    app.store.compact_search_indexes()
    assert secret.encode() not in db_bytes(app.settings.home)
