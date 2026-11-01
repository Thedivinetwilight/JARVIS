"""Thin HTTP adapter over :class:`app.jarvis.app.App`.

Deliberately small: HTTP parsing, body limits, timeouts and response writing. Every
security decision happens in ``App.request`` so it can be tested without a socket. The
server is single-owner and local-first: it binds loopback by default, refuses a
non-loopback bind without TLS (config validation), and sets no CORS headers because no
browser client exists.
"""

from __future__ import annotations

import logging
import socket
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .app import App, Response
from .config import Config

LOGGER = logging.getLogger("jarvis.http")

COMMON_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


class JarvisHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, app: App, config: Config) -> None:
        self.app = app
        self.config = config
        self.request_timeout_s = config.server.request_timeout_s
        self.max_request_bytes = config.server.max_request_bytes
        if ":" in config.server.bind:
            self.address_family = socket.AF_INET6
        super().__init__(address, JarvisRequestHandler)

    def server_bind(self) -> None:  # keep the DNS lookup out of startup logs
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        super().server_bind()


class JarvisRequestHandler(BaseHTTPRequestHandler):
    server_version = "Jarvis/0.1.0"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------------ plumbing
    @property
    def app(self) -> App:
        return self.server.app  # type: ignore[attr-defined]

    @property
    def config(self) -> Config:
        return self.server.config  # type: ignore[attr-defined]

    def setup(self) -> None:
        super().setup()
        try:
            self.connection.settimeout(self.server.request_timeout_s)  # type: ignore[attr-defined]
        except OSError:  # pragma: no cover - defensive
            pass

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - stdlib signature
        # Never log headers or bodies: the default implementation logs the request line only,
        # and even that is routed through our redacting logger.
        LOGGER.info("%s %s", self.address_string(), fmt % args)

    # ------------------------------------------------------------------ verbs
    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        self._handle()

    def do_POST(self) -> None:  # noqa: N802
        self._handle()

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle()

    def do_PUT(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_PATCH(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_HEAD(self) -> None:  # noqa: N802
        self._method_not_allowed()

    # ------------------------------------------------------------------ request handling
    def _method_not_allowed(self) -> None:
        payload = b'{"error": {"code": "method_not_allowed", "message": "method not allowed"}}'
        self.send_response(405)
        for key, value in COMMON_HEADERS.items():
            self.send_header(key, value)
        self.send_header("Allow", "GET, POST, DELETE")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_body(self) -> bytes | None:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return b""
        try:
            length = int(raw_length)
        except ValueError:
            self._fail(400, "bad_content_length", "Content-Length is not an integer")
            return None
        if length < 0:
            self._fail(400, "bad_content_length", "Content-Length is negative")
            return None
        if length > self.server.max_request_bytes:  # type: ignore[attr-defined]
            self._fail(413, "body_too_large", "request body is too large")
            return None
        try:
            return self.rfile.read(length)
        except (socket.timeout, TimeoutError):
            self._fail(408, "request_timeout", "request body timed out")
            return None

    def _fail(self, status: int, code: str, message: str) -> None:
        payload = ('{"error": {"code": "%s", "message": "%s"}}' % (code, message)).encode("utf-8")
        self.send_response(status)
        for key, value in COMMON_HEADERS.items():
            self.send_header(key, value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(payload)
        except OSError:  # pragma: no cover - client went away
            pass
        self.close_connection = True

    def _handle(self) -> None:
        body = self._read_body()
        if body is None:
            return
        headers = {key.lower(): value for key, value in self.headers.items()}
        response: Response = self.app.request(self.command, self.path, headers, body)
        self.send_response(response.status)
        already_sent = {name.lower() for name in COMMON_HEADERS}
        for key, value in COMMON_HEADERS.items():
            self.send_header(key, value)
        for key, value in response.headers.items():
            # Never emit the same header twice: a duplicated Cache-Control is ambiguous.
            if key.lower() in already_sent:
                continue
            self.send_header(key, value)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(response.body)))
        self.end_headers()
        try:
            self.wfile.write(response.body)
        except OSError:  # pragma: no cover - client went away
            pass
        LOGGER.info("%s %s -> %s", self.command, self.path.split("?")[0], response.status)


def build_server(config: Config, app: App) -> JarvisHTTPServer:
    server = JarvisHTTPServer((config.server.bind, config.server.port), app, config)
    if config.server.tls_enabled:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(config.server.tls_cert, config.server.tls_key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    return server


def bound_address(server: JarvisHTTPServer) -> tuple[str, int]:
    host, port = server.socket.getsockname()[:2]
    return str(host), int(port)
