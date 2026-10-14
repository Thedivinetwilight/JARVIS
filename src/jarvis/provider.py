"""Optional Gemini REST provider. Standard library only; no SDK required."""
from __future__ import annotations
import json
import urllib.error
import urllib.request

class ProviderError(RuntimeError):
    pass

class GeminiProvider:
    def __init__(self, api_key: str, model: str, timeout: float = 30):
        if not api_key:
            raise ValueError("Gemini API key is required")
        self.api_key, self.model, self.timeout = api_key, model, timeout

    def generate(self, system: str, user: str) -> str:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": 0.2, "maxOutputTokens": 2048}}
        request = urllib.request.Request(url, data=json.dumps(body).encode(),
                    headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Never echo request URL because it contains the API key.
            detail = exc.read(1000).decode("utf-8", errors="replace")
            raise ProviderError(f"Gemini HTTP {exc.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(f"Gemini request failed: {type(exc).__name__}") from None
        try:
            return "".join(part.get("text", "") for part in payload["candidates"][0]["content"]["parts"]).strip()
        except (KeyError, IndexError, TypeError):
            raise ProviderError("Gemini returned an unexpected response shape") from None
