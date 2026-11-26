"""Provider gateway: bounded retry, fallback, honest attribution and key handling.

Two layers are exercised: a stub provider pair (deterministic failure injection) and
real HTTP against the local fake model server, so retry/fallback are observed on the
wire rather than assumed.
"""
from __future__ import annotations

import pytest

from jarvis.config import Settings
from jarvis.provider import (
    GeminiProvider,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    OpenAICompatibleProvider,
    ProviderHTTPError,
    ProviderMalformedResponse,
    ProviderRefused,
    ProviderTimeout,
    ProviderUnavailable,
    ResilientProvider,
    ScriptedProvider,
    build_provider,
)

REQUEST = ModelRequest(system="You are JARVIS.", messages=(ModelMessage("user", "hello"),))


class StubProvider:
    """A provider whose behaviour is a queue of outcomes (external boundary double)."""

    is_remote = False

    def __init__(self, name: str, outcomes: list) -> None:
        self.name = name
        self.model = f"{name}-model"
        self.outcomes = list(outcomes)
        self.calls = 0

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, BaseException):
            raise outcome
        return ModelResponse(text=outcome, provider=self.name, model=self.model, latency_ms=1.0, usage={})


def test_transient_failure_is_retried_then_succeeds():
    primary = StubProvider("primary", [ProviderUnavailable("down"), "recovered"])
    provider = ResilientProvider([primary], max_retries=1, backoff_base=0.0)
    response = provider.generate(REQUEST)
    assert response.text == "recovered"
    assert primary.calls == 2
    assert [a["outcome"] for a in provider.attempts] == ["unavailable", "ok"]


def test_non_transient_failure_is_not_retried():
    primary = StubProvider("primary", [ProviderRefused("blocked")])
    provider = ResilientProvider([primary], max_retries=3, backoff_base=0.0)
    with pytest.raises(ProviderRefused):
        provider.generate(REQUEST)
    assert primary.calls == 1


def test_fallback_provider_takes_over_after_retries_are_exhausted():
    primary = StubProvider("primary", [ProviderTimeout("slow")])
    secondary = StubProvider("secondary", ["from fallback"])
    provider = ResilientProvider([primary, secondary], max_retries=1, backoff_base=0.0)
    response = provider.generate(REQUEST)
    assert response.text == "from fallback"
    assert primary.calls == 2 and secondary.calls == 1
    assert response.provider == "secondary"                   # attribution is truthful
    assert response.usage["_via"] == "resilient"


def test_all_providers_failing_raises_the_last_error():
    primary = StubProvider("primary", [ProviderUnavailable("down")])
    secondary = StubProvider("secondary", [ProviderMalformedResponse("bad json")])
    provider = ResilientProvider([primary, secondary], max_retries=0)
    with pytest.raises(ProviderMalformedResponse):
        provider.generate(REQUEST)


def test_retries_are_bounded_for_an_always_failing_provider():
    primary = StubProvider("primary", [ProviderUnavailable("down")])
    provider = ResilientProvider([primary], max_retries=2, backoff_base=0.0)
    with pytest.raises(ProviderUnavailable):
        provider.generate(REQUEST)
    assert primary.calls == 3                                 # 1 attempt + 2 retries, no more


def test_real_http_500_is_retried_and_falls_back(fake_model):
    from conftest import FakeModelServer
    healthy = FakeModelServer()
    healthy.reply = "fallback answer"
    try:
        fake_model.mode = "error500"                       # the primary endpoint is down
        primary = GeminiProvider("k" * 20, "gemini-test", timeout=5, base_url=fake_model.gemini_base)
        secondary = OpenAICompatibleProvider(healthy.openai_base, "local-model", timeout=5)
        provider = ResilientProvider([primary, secondary], max_retries=1, backoff_base=0.0)
        response = provider.generate(REQUEST)
        assert response.text == "fallback answer" and response.provider == "openai_compatible"
        assert [r["path"] for r in fake_model.requests] == [
            "/v1beta/models/gemini-test:generateContent"] * 2        # one attempt + one retry
        assert [r["path"] for r in healthy.requests] == ["/v1/chat/completions"]
    finally:
        healthy.close()


def test_build_provider_chains_a_primary_and_a_fallback(fake_model):
    settings = Settings(home=fake_model.__class__ and __import__("pathlib").Path("/tmp/x"),
                        workspace=__import__("pathlib").Path("/tmp/x"), network_enabled=True,
                        gemini_api_key="AIza" + "Z" * 30, gemini_model="gemini-test",
                        host="127.0.0.1", port=8765, log_level="INFO",
                        gemini_base_url=fake_model.gemini_base,
                        openai_base_url=fake_model.openai_base, openai_model="local-model")
    provider = build_provider(settings)
    assert isinstance(provider, ResilientProvider) and len(provider.providers) == 2
    assert [p.name for p in provider.providers] == ["gemini", "openai_compatible"]


def test_remote_providers_stay_disabled_when_network_is_off(fake_model):
    settings = Settings(home=__import__("pathlib").Path("/tmp/x"), workspace=__import__("pathlib").Path("/tmp/x"),
                        network_enabled=False, gemini_api_key="AIza" + "Z" * 30, gemini_model="gemini-test",
                        host="127.0.0.1", port=8765, log_level="INFO",
                        provider="gemini",
                        gemini_base_url="https://generativelanguage.googleapis.com/v1beta")
    assert build_provider(settings) is None


def test_api_keys_are_sent_in_headers_never_in_the_url(fake_model):
    key = "AIza" + "Q" * 30
    provider = GeminiProvider(key, "gemini-test", timeout=5, base_url=fake_model.gemini_base)
    provider.generate(REQUEST)
    request = fake_model.requests[-1]
    assert key not in request["path"]
    lowered = {k.lower(): v for k, v in request["headers"].items()}
    assert lowered.get("x-goog-api-key") == key

    token = "sk-secret-" + "T" * 20
    openai = OpenAICompatibleProvider(fake_model.openai_base, "local", api_key=token, timeout=5)
    openai.generate(REQUEST)
    request = fake_model.requests[-1]
    assert token not in request["path"]
    lowered = {k.lower(): v for k, v in request["headers"].items()}
    assert lowered.get("authorization") == f"Bearer {token}"


def test_provider_errors_do_not_echo_the_api_key(fake_model):
    key = "AIza" + "S" * 30
    fake_model.mode = "error500"
    provider = GeminiProvider(key, "gemini-test", timeout=5, base_url=fake_model.gemini_base)
    with pytest.raises(ProviderHTTPError) as info:
        provider.generate(REQUEST)
    assert key not in str(info.value)


def test_scripted_provider_still_works_for_offline_use():
    scripted = ScriptedProvider(["first", "second"])
    assert scripted.generate(REQUEST).text == "first"
    assert scripted.generate(REQUEST).text == "second"
