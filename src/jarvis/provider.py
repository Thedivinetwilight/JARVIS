"""Model providers behind an explicit request/response contract. Standard library only.

A provider drafts language. It does not own state, permissions, persistence or
validation — the kernel does. Contract:

    generate(ModelRequest) -> ModelResponse     or raises a ProviderError subclass

Errors are classified so the kernel can report them honestly and continue in a
degraded mode: ProviderTimeout, ProviderUnavailable (connection/DNS),
ProviderHTTPError (status code), ProviderMalformedResponse, ProviderRefused (blocked or
empty). Error messages never include request URLs, headers, API keys, or prompts.

Data-locality rule: a provider whose endpoint is not on the loopback interface is
*remote*. Remote providers are only constructed when ``JARVIS_NETWORK_ENABLED=true``
(this preserves v0.2's rule that cloud calls require explicit opt-in).
"""
from __future__ import annotations

import ipaddress
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .redact import redact_text

MAX_RESPONSE_BYTES = 2_000_000


class ProviderError(RuntimeError):
    kind = "error"


class ProviderTimeout(ProviderError):
    kind = "timeout"


class ProviderUnavailable(ProviderError):
    kind = "unavailable"


class ProviderHTTPError(ProviderError):
    kind = "http_error"

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


class ProviderMalformedResponse(ProviderError):
    kind = "malformed_response"


class ProviderRefused(ProviderError):
    kind = "refused"


@dataclass(frozen=True)
class ModelMessage:
    role: str          # "user" | "assistant"
    content: str


@dataclass(frozen=True)
class ModelRequest:
    system: str
    messages: tuple[ModelMessage, ...]
    max_output_tokens: int = 1024
    temperature: float = 0.2
    purpose: str = "respond"

    def __post_init__(self) -> None:
        if not self.messages or self.messages[-1].role != "user":
            raise ValueError("ModelRequest must end with a user message")
        if any(m.role not in {"user", "assistant"} for m in self.messages):
            raise ValueError("ModelRequest roles must be 'user' or 'assistant'")
        if not 1 <= self.max_output_tokens <= 8192:
            raise ValueError("max_output_tokens must be within 1-8192")


@dataclass(frozen=True)
class ModelResponse:
    text: str
    provider: str
    model: str
    latency_ms: float
    finish_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


class ModelProvider(Protocol):
    name: str
    model: str
    is_remote: bool

    def generate(self, request: ModelRequest) -> ModelResponse: ...


def is_loopback_url(url: str) -> bool:
    host = urllib.parse.urlparse(url).hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _check_base_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Provider base URL must be an http(s) URL")
    if parsed.scheme == "http" and not is_loopback_url(url):
        raise ValueError("Plain http is only allowed for loopback provider endpoints")
    if parsed.username or parsed.password:
        raise ValueError("Provider base URL must not embed credentials")
    return url.rstrip("/")


def _post_json(url: str, body: dict[str, Any], headers: dict[str, str], timeout: float, label: str) -> dict[str, Any]:
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = redact_text(exc.read(600).decode("utf-8", errors="replace"))
        except Exception:  # noqa: BLE001
            pass
        raise ProviderHTTPError(f"{label} HTTP {exc.code}: {detail[:300]}".strip(), exc.code) from None
    except (TimeoutError, socket.timeout):
        raise ProviderTimeout(f"{label} did not respond within {timeout:g}s") from None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise ProviderTimeout(f"{label} did not respond within {timeout:g}s") from None
        raise ProviderUnavailable(f"{label} unreachable: {type(exc.reason).__name__}") from None
    except OSError as exc:
        raise ProviderUnavailable(f"{label} request failed: {type(exc).__name__}") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ProviderMalformedResponse(f"{label} response exceeded {MAX_RESPONSE_BYTES} bytes")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ProviderMalformedResponse(f"{label} returned a non-JSON response") from None
    if not isinstance(payload, dict):
        raise ProviderMalformedResponse(f"{label} returned an unexpected JSON shape")
    return payload


class GeminiProvider:
    """Google Gemini ``generateContent`` over REST.

    The API key is sent in the ``x-goog-api-key`` header (Google's documented method),
    not in the URL as v0.2 did, so it cannot leak through URL logging.
    """

    name = "gemini"

    def __init__(self, api_key: str, model: str, timeout: float = 30,
                 base_url: str = "https://generativelanguage.googleapis.com/v1beta"):
        if not api_key:
            raise ValueError("Gemini API key is required")
        self.api_key, self.model, self.timeout = api_key, model, float(timeout)
        self.base_url = _check_base_url(base_url)
        self.is_remote = not is_loopback_url(self.base_url)

    def generate(self, request: ModelRequest | str, user: str | None = None) -> ModelResponse:
        if isinstance(request, str):  # v0.2 call style: generate(system, user)
            request = ModelRequest(system=request, messages=(ModelMessage("user", user or ""),))
        model = urllib.parse.quote(self.model, safe="-._")
        body = {"systemInstruction": {"parts": [{"text": request.system}]},
                "contents": [{"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.content}]}
                             for m in request.messages],
                "generationConfig": {"temperature": request.temperature,
                                     "maxOutputTokens": request.max_output_tokens}}
        started = time.monotonic()
        payload = _post_json(f"{self.base_url}/models/{model}:generateContent", body,
                             {"x-goog-api-key": self.api_key}, self.timeout, "Gemini")
        latency = (time.monotonic() - started) * 1000
        feedback = payload.get("promptFeedback") or {}
        if isinstance(feedback, dict) and feedback.get("blockReason"):
            raise ProviderRefused(f"Gemini blocked the request: {feedback.get('blockReason')}")
        try:
            candidate = payload["candidates"][0]
            text = "".join(part.get("text", "") for part in candidate["content"]["parts"]).strip()
        except (KeyError, IndexError, TypeError, AttributeError):
            raise ProviderMalformedResponse("Gemini returned an unexpected response shape") from None
        if not text:
            raise ProviderRefused(f"Gemini returned no text (finishReason={candidate.get('finishReason')})")
        return ModelResponse(text=text, provider=self.name, model=self.model, latency_ms=round(latency, 1),
                             finish_reason=candidate.get("finishReason"),
                             usage=payload.get("usageMetadata") if isinstance(payload.get("usageMetadata"), dict) else {})


class OpenAICompatibleProvider:
    """Any server implementing ``POST {base}/chat/completions`` (Ollama, llama.cpp server, vLLM, ...)."""

    name = "openai_compatible"

    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: float = 60):
        if not model:
            raise ValueError("A model name is required for the OpenAI-compatible provider")
        self.base_url = _check_base_url(base_url)
        self.model, self.api_key, self.timeout = model, api_key, float(timeout)
        self.is_remote = not is_loopback_url(self.base_url)

    def generate(self, request: ModelRequest) -> ModelResponse:
        body = {"model": self.model, "temperature": request.temperature, "max_tokens": request.max_output_tokens,
                "messages": [{"role": "system", "content": request.system},
                             *[{"role": m.role, "content": m.content} for m in request.messages]]}
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        started = time.monotonic()
        payload = _post_json(f"{self.base_url}/chat/completions", body, headers, self.timeout, "Model server")
        latency = (time.monotonic() - started) * 1000
        try:
            choice = payload["choices"][0]
            text = (choice["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError, AttributeError):
            raise ProviderMalformedResponse("Model server returned an unexpected response shape") from None
        if not text:
            raise ProviderRefused(f"Model server returned no text (finish_reason={choice.get('finish_reason')})")
        return ModelResponse(text=text, provider=self.name, model=self.model, latency_ms=round(latency, 1),
                             finish_reason=choice.get("finish_reason"),
                             usage=payload.get("usage") if isinstance(payload.get("usage"), dict) else {})


class ScriptedProvider:
    """Deterministic provider for tests and offline evaluation (never used by default).

    ``script`` items are strings (returned as text), exceptions (raised), or callables
    taking the ModelRequest. Every request is kept in ``requests`` for inspection.
    """

    name = "scripted"
    is_remote = False

    def __init__(self, script: list[Any] | Callable[[ModelRequest], Any], model: str = "scripted-v1"):
        self.script, self.model = script, model
        self.requests: list[ModelRequest] = []

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if callable(self.script):
            item = self.script(request)
        elif self.script:
            item = self.script.pop(0)
        else:
            raise ProviderUnavailable("Scripted provider has no responses left")
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            item = item(request)
        return ModelResponse(text=str(item), provider=self.name, model=self.model, latency_ms=0.0, finish_reason="stop")


def build_provider(settings: Any) -> ModelProvider | None:
    """Construct the configured provider, or ``None`` (the kernel then runs without a model).

    ``JARVIS_PROVIDER=auto`` (default) keeps v0.2 behaviour: Gemini when network is
    enabled and a key is present; additionally an OpenAI-compatible endpoint when
    ``JARVIS_OPENAI_BASE_URL`` is explicitly set (remote endpoints still need network).
    """
    choice = settings.provider
    if choice == "none":
        return None
    if choice in {"auto", "gemini"} and settings.gemini_api_key:
        provider = GeminiProvider(settings.gemini_api_key, settings.gemini_model, timeout=settings.provider_timeout,
                                  base_url=settings.gemini_base_url)
        if not provider.is_remote or settings.network_enabled:
            return provider
        if choice == "gemini":
            return None
    if choice in {"auto", "openai_compatible"} and settings.openai_base_url:
        provider = OpenAICompatibleProvider(settings.openai_base_url, settings.openai_model,
                                            api_key=settings.openai_api_key, timeout=settings.provider_timeout)
        if not provider.is_remote or settings.network_enabled:
            return provider
    return None
