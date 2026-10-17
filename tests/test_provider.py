"""Provider adapters against a real local HTTP server (no mocks of urllib)."""
from __future__ import annotations

import socket

import pytest

from conftest import FAKE_GEMINI_KEY, make_settings
from jarvis.provider import (GeminiProvider, ModelMessage, ModelRequest, OpenAICompatibleProvider, ProviderHTTPError,
                             ProviderMalformedResponse, ProviderRefused, ProviderTimeout, ProviderUnavailable,
                             build_provider)


def request(text="hi"):
    return ModelRequest(system="sys", messages=(ModelMessage("user", text),))


def test_gemini_success_uses_header_auth(fake_model):
    provider = GeminiProvider(FAKE_GEMINI_KEY, "gemini-test", timeout=5, base_url=fake_model.gemini_base)
    response = provider.generate(request())
    assert response.text == "Hello from the fake model." and response.provider == "gemini"
    sent = fake_model.requests[-1]
    headers = {k.lower(): v for k, v in sent["headers"].items()}
    assert headers["x-goog-api-key"] == FAKE_GEMINI_KEY and "key=" not in sent["path"]
    assert sent["path"] == "/v1beta/models/gemini-test:generateContent"
    assert sent["body"]["systemInstruction"]["parts"][0]["text"] == "sys"


def test_openai_compatible_success(fake_model):
    provider = OpenAICompatibleProvider(fake_model.openai_base, "llama3.2", timeout=5)
    response = provider.generate(request("hello"))
    assert response.text == "Hello from the fake model." and not provider.is_remote
    body = fake_model.requests[-1]["body"]
    assert body["messages"][0] == {"role": "system", "content": "sys"} and body["model"] == "llama3.2"


@pytest.mark.parametrize(("mode", "delay", "error"), [("ok", 1.0, ProviderTimeout), ("error500", 0, ProviderHTTPError),
                                                      ("malformed", 0, ProviderMalformedResponse),
                                                      ("blocked", 0, ProviderRefused)])
def test_failures_are_classified(fake_model, mode, delay, error):
    fake_model.mode, fake_model.delay = mode, delay
    provider = GeminiProvider(FAKE_GEMINI_KEY, "gemini-test", timeout=0.3, base_url=fake_model.gemini_base)
    with pytest.raises(error) as info:
        provider.generate(request())
    assert FAKE_GEMINI_KEY not in str(info.value)


def test_unreachable_server_is_unavailable():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    provider = OpenAICompatibleProvider(f"http://127.0.0.1:{port}/v1", "m", timeout=1)
    with pytest.raises(ProviderUnavailable):
        provider.generate(request())


def test_base_url_rules():
    with pytest.raises(ValueError):
        OpenAICompatibleProvider("http://example.com/v1", "m")        # plain http only for loopback
    with pytest.raises(ValueError):
        OpenAICompatibleProvider("https://user:pass@example.com/v1", "m")
    assert OpenAICompatibleProvider("https://example.com/v1", "m").is_remote


def test_build_provider_respects_network_opt_in(tmp_path, fake_model):
    assert build_provider(make_settings(tmp_path)) is None
    remote = make_settings(tmp_path, gemini_api_key=FAKE_GEMINI_KEY)               # default Google endpoint
    assert build_provider(remote) is None                                           # network disabled
    assert build_provider(make_settings(tmp_path, gemini_api_key=FAKE_GEMINI_KEY, network_enabled=True)).name == "gemini"
    local = make_settings(tmp_path, openai_base_url=fake_model.openai_base, openai_model="llama3.2")
    assert build_provider(local).name == "openai_compatible"                         # loopback needs no opt-in
    remote_oa = make_settings(tmp_path, openai_base_url="https://api.example.com/v1", openai_model="m")
    assert build_provider(remote_oa) is None
    assert build_provider(make_settings(tmp_path, provider="none", openai_base_url=fake_model.openai_base,
                                        openai_model="m")) is None
