"""HTTP client for one local J.A.R.V.I.S. server.

Deliberate properties, each of which is tested:

* **One host.** Every request goes to the configured server's scheme, host and port. A redirect is an
  error, not a hop, so a server-side 302 can never carry the bearer token to another host.
* **No proxy, no cookies, no connection reuse.** The owner's machine may have a proxy configured for
  the internet; this client must never use it, and must never look like a browser.
* **Bounded.** Every request has a timeout, and every response body is capped, because a client that
  blocks forever stops showing reminders.
* **Typed failures.** ``401``/``403`` become :class:`AuthRejected` with the server's own reason,
  transport problems become :class:`ServerUnreachable`, and anything unparseable becomes
  :class:`ProtocolError` — never a silent empty inbox.

The server sends JSON only and exposes no chat route; this client asks for nothing else.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any

from . import PLATFORM_LABEL, VERSION
from .config import CompanionConfig
from .errors import AuthRejected, ProtocolError, ServerUnreachable

#: The narrowest scope that can receive and acknowledge reminders.
REQUIRED_SCOPE = "reminders.receive"

MAX_RESPONSE_BYTES = 1_048_576
USER_AGENT = f"jarvis-companion/{VERSION} (local; no telemetry)"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects outright: following one would move the bearer token to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, D102
        raise ProtocolError(
            f"the server answered {code} redirect to {newurl!r}; the companion does not follow "
            "redirects because the device token must not leave the configured host",
            detail={"status": code, "location": str(newurl)},
        )


def build_opener() -> urllib.request.OpenerDirector:
    opener = urllib.request.OpenerDirector()
    opener.add_handler(urllib.request.UnknownHandler())
    opener.add_handler(urllib.request.HTTPHandler())
    opener.add_handler(urllib.request.HTTPSHandler())
    opener.add_handler(_NoRedirect())
    # Without the default error handler a 4xx/5xx makes ``open()`` return None instead of raising,
    # which would look like an empty response. The error is information here, not an exception to
    # lose: the server puts the reason in the body and the client maps it to a typed failure.
    opener.add_handler(urllib.request.HTTPDefaultErrorHandler())
    opener.add_handler(urllib.request.HTTPErrorProcessor())
    # No ProxyHandler is installed, so environment proxies are never consulted.
    return opener


class ServerApi:
    """The whole server surface this client uses: pair, inbox, ack, self-revoke, health."""

    def __init__(self, config: CompanionConfig, *, timeout: float = 10.0,
                 opener: urllib.request.OpenerDirector | None = None) -> None:
        self.config = config
        self.timeout = float(timeout)
        self._opener = opener
        self._origin = (config.base.split("://", 1)[0].lower(), config.host.lower(), config.port)

    # ------------------------------------------------------------------ transport
    def _opener_for_request(self) -> urllib.request.OpenerDirector:
        if self._opener is None:
            self._opener = build_opener()
        return self._opener

    def _request(self, method: str, path: str, *, token: str | None = None,
                 body: dict | None = None) -> tuple[int, dict]:
        if not path.startswith("/"):
            raise ProtocolError(f"refusing a non-absolute API path {path!r}")
        url = f"{self.config.base}{path}"
        data = None
        headers = {"accept": "application/json", "user-agent": USER_AGENT,
                   "connection": "close"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["content-type"] = "application/json"
        if token:
            headers["authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener_for_request().open(request, timeout=self.timeout) as response:
                status = int(response.status)
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:                     # 4xx/5xx with a body
            status = int(exc.code)
            raw = exc.read(MAX_RESPONSE_BYTES + 1)
            return status, self._parse(raw, status=status, expect_error=True)
        except urllib.error.URLError as exc:
            raise ServerUnreachable(
                f"cannot reach {self.config.base}: {getattr(exc, 'reason', exc)}",
                detail={"url": self.config.base},
            ) from exc
        except (socket.timeout, TimeoutError) as exc:
            raise ServerUnreachable(
                f"{self.config.base} did not answer within {self.timeout:g}s",
                detail={"url": self.config.base, "timeout_seconds": self.timeout},
            ) from exc
        except OSError as exc:                                     # connection refused, DNS, reset
            raise ServerUnreachable(
                f"cannot reach {self.config.base}: {exc}", detail={"url": self.config.base}
            ) from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ProtocolError("the server response exceeded the companion's size limit",
                                detail={"limit_bytes": MAX_RESPONSE_BYTES})
        return status, self._parse(raw, status=status, expect_error=False)

    @staticmethod
    def _parse(raw: bytes, *, status: int, expect_error: bool) -> dict:
        if not raw:
            if expect_error:
                return {"error": {"code": f"http_{status}", "message": "no response body"}}
            raise ProtocolError(f"the server answered {status} with an empty body")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ProtocolError(
                "the server response was not JSON; the companion only speaks the JSON API",
                detail={"status": status},
            ) from exc
        if not isinstance(payload, dict):
            raise ProtocolError("the server response must be a JSON object",
                                detail={"status": status})
        return payload

    def _fail(self, status: int, payload: dict, *, what: str) -> None:
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        code = str(error.get("code") or f"http_{status}")
        message = str(error.get("message") or f"{what} failed with HTTP {status}")
        if status == 401:
            kind = "token_expired" if code == "token_expired" else (
                "token_revoked" if code == "token_revoked" else "unauthenticated")
            raise AuthRejected(f"{what}: {message}", kind=kind,
                               detail={"code": code, "status": status})
        if status == 403:
            raise AuthRejected(
                f"{what}: {message}",
                kind="scope_denied",
                detail={"code": code, "status": status, "required_scope": REQUIRED_SCOPE},
            )
        if status >= 500:
            raise ServerUnreachable(
                f"{what}: the server is not ready ({code}): {message}",
                detail={"code": code, "status": status},
            )
        raise ProtocolError(
            f"{what}: unexpected HTTP {status} ({code}): {message}",
            detail={"code": code, "status": status},
        )

    # ------------------------------------------------------------------ routes
    def health(self) -> dict:
        status, payload = self._request("GET", "/healthz")
        if status != 200:
            self._fail(status, payload, what="health check")
        return payload

    def pair(self, *, code: str, label: str, platform: str = PLATFORM_LABEL) -> dict:
        if not str(code).strip():
            raise ProtocolError("a pairing code is required")
        status, payload = self._request(
            "POST", "/v1/pairing",
            body={"code": str(code).strip(), "label": str(label), "platform": platform},
        )
        if status != 201:
            if status == 401:
                error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
                raise AuthRejected(
                    f"pairing refused: {error.get('message', 'the code was not accepted')}",
                    kind="pairing_refused",
                    detail={"code": str(error.get("code") or "http_401")},
                )
            self._fail(status, payload, what="pairing")
        client = payload.get("client") if isinstance(payload.get("client"), dict) else None
        token = payload.get("token")
        if not client or not isinstance(token, str) or not token:
            raise ProtocolError("the pairing response did not contain a client and a token")
        for key in ("id", "label", "platform", "scopes"):
            if key not in client:
                raise ProtocolError(f"the pairing response's client has no {key!r}")
        return payload

    def inbox(self, token: str, *, limit: int = 50) -> dict:
        status, payload = self._request("GET", f"/v1/reminders/inbox?limit={int(limit)}",
                                       token=token)
        if status != 200:
            self._fail(status, payload, what="reminder inbox")
        if not isinstance(payload.get("events"), list):
            raise ProtocolError("the inbox response has no events list")
        return payload

    def ack(self, token: str, delivery_id: str) -> dict:
        status, payload = self._request(
            "POST", f"/v1/reminders/inbox/{delivery_id}/ack", token=token
        )
        if status != 200:
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            if status == 409 and str(error.get("code")) == "delivery_closed":
                raise ProtocolError(
                    "the server closed this delivery before the acknowledgement arrived",
                    detail={"code": "delivery_closed", **(error.get("detail") or {})},
                )
            if status == 404:
                raise ProtocolError(
                    "the server does not know this delivery for this client",
                    detail={"code": str(error.get("code") or "delivery_not_found")},
                )
            self._fail(status, payload, what="acknowledgement")
        delivery = payload.get("delivery")
        if not isinstance(delivery, dict) or not delivery.get("delivery_id"):
            raise ProtocolError("the acknowledgement response has no delivery")
        return delivery

    def revoke_self(self, token: str, client_id: str) -> dict:
        status, payload = self._request(
            "POST", f"/v1/clients/{client_id}/revoke", token=token
        )
        if status != 200:
            self._fail(status, payload, what="revoking this client")
        return payload


__all__ = ["MAX_RESPONSE_BYTES", "REQUIRED_SCOPE", "ServerApi", "USER_AGENT", "build_opener"]
