"""Allow-listed web retrieval (Mission 003).

The server is a real local HTTP server; the policy is the real one. What is checked is what
matters for safety: an allow-list that is a bare hostname (no wildcards, no schemes), address
refusals *after* DNS resolution, per-hop redirect revalidation, byte and time caps, and
provenance on everything returned.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from jarvis.web import (MAX_TEXT_CHARS, REDIRECT_STATUSES, WebDenied, WebError, WebFetcher,
                        WebPolicy, extract_text)

PAGE = ("<html><head><title>Release notes</title></head><body>"
        "<script>alert('injected');</script>"
        "<p>The release train leaves on Friday at 18:00.</p>"
        "<p>Two unresolved issues remain in the erasure work.</p>"
        "</body></html>")


class _Server:
    def __init__(self) -> None:
        self.requests: list[str] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                server.requests.append(self.path)
                if self.path == "/page":
                    Handler._send(self, 200, PAGE.encode(), "text/html; charset=utf-8")
                elif self.path == "/plain":
                    Handler._send(self, 200, b"plain text about leases\n", "text/plain")
                elif self.path == "/json":
                    Handler._send(self, 200, b'{"note": "json payload"}', "application/json")
                elif self.path == "/binary":
                    Handler._send(self, 200, b"%PDF-1.4 payload", "application/pdf")
                elif self.path == "/big":
                    Handler._send(self, 200, b"x" * 20000, "text/html")
                elif self.path == "/hop":
                    Handler._send(self, 302, b"", "text/html", {"Location": "/page"})
                elif self.path == "/offsite":
                    Handler._send(self, 302, b"", "text/html",
                                  {"Location": "http://169.254.169.254/latest/meta-data"})
                elif self.path == "/loop":
                    Handler._send(self, 302, b"", "text/html", {"Location": "/loop"})
                elif self.path == "/slow":
                    import time
                    time.sleep(1.0)
                    Handler._send(self, 200, b"slow page", "text/html")
                else:
                    Handler._send(self, 404, b"missing", "text/html")

            @staticmethod
            def _send(handler, status, body, content_type, headers=None):
                handler.send_response(status)
                handler.send_header("Content-Type", content_type)
                handler.send_header("Content-Length", str(len(body)))
                for key, value in (headers or {}).items():
                    handler.send_header(key, value)
                handler.end_headers()
                handler.wfile.write(body)

            def log_message(self, *_args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.httpd.shutdown()
        self.httpd.server_close()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"


@pytest.fixture
def server():
    with _Server() as instance:
        yield instance


def loopback_policy(**overrides) -> WebPolicy:
    values = dict(allowlist=("127.0.0.1",), allow_loopback=True, network_enabled=True)
    values.update(overrides)
    return WebPolicy(**values)


# ---------------------------------------------------------------------------------------
# allow-list validation (configuration is not a place for surprises)
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("entry", ["http://example.com", "example.com/path", "*.example.com",
                                   "example.com:8080", "", "  ", "exa mple.com"])
def test_invalid_allowlist_entries_are_rejected(entry):
    with pytest.raises(WebError):
        WebPolicy(allowlist=(entry,), network_enabled=True)


@pytest.mark.parametrize("entry", ["example.com", "sub.example.co.uk", "localhost", "127.0.0.1"])
def test_bare_hostnames_are_accepted(entry):
    policy = WebPolicy(allowlist=(entry,), network_enabled=True)
    assert entry in policy.allowlist


def test_allowlist_matching_is_exact_not_a_loose_suffix():
    policy = WebPolicy(allowlist=("example.com",), network_enabled=True)
    assert policy.host_allowed("example.com")
    assert policy.host_allowed("EXAMPLE.com")
    assert policy.host_allowed("docs.example.com")          # a subdomain, by design
    assert not policy.host_allowed("evil-example.com")      # suffix only: not a subdomain
    assert not policy.host_allowed("example.com.evil.test")
    assert not policy.host_allowed("notexample.com")


def test_an_empty_allowlist_disables_retrieval():
    policy = WebPolicy(allowlist=(), network_enabled=True)
    assert policy.enabled is False
    with pytest.raises(WebDenied):
        WebFetcher(policy).get("http://example.com/")


def test_network_disabled_refuses_before_any_socket():
    policy = WebPolicy(allowlist=("example.com",), network_enabled=False)
    with pytest.raises(WebDenied):
        WebFetcher(policy).get("http://example.com/")


# ---------------------------------------------------------------------------------------
# scheme, host and address policy
# ---------------------------------------------------------------------------------------
def test_only_http_and_https_are_allowed(server):
    fetcher = WebFetcher(loopback_policy())
    for url in ("file:///etc/passwd", "ftp://127.0.0.1/x", "gopher://127.0.0.1/x",
                "javascript:alert(1)"):
        with pytest.raises(WebDenied):
            fetcher.get(url)


def test_a_host_outside_the_allowlist_is_refused_without_a_request(server):
    fetcher = WebFetcher(WebPolicy(allowlist=("example.com",), network_enabled=True))
    with pytest.raises(WebDenied):
        fetcher.get(server.url("/page"))
    assert server.requests == []


@pytest.mark.parametrize("host", ["localhost", "metadata.google.internal", "printer.local",
                                  "internal-host"])
def test_local_and_internal_names_are_refused(host):
    with pytest.raises(WebDenied):
        WebFetcher(WebPolicy(allowlist=(host,), network_enabled=True)).get(f"http://{host}/x")


@pytest.mark.parametrize("address", ["169.254.169.254", "10.0.0.5", "192.168.1.1", "172.16.9.9",
                                     "0.0.0.0", "[::1]", "100.64.0.1", "[fd00::1]"])
def test_private_and_link_local_addresses_are_refused(address):
    host = address.strip("[]")
    bracketed = f"[{host}]" if ":" in host else host
    with pytest.raises(WebDenied):
        WebFetcher(WebPolicy(allowlist=(host,), network_enabled=True)).get(
            f"http://{bracketed}/x")


def test_loopback_requires_explicit_authorisation(server):
    policy = WebPolicy(allowlist=("127.0.0.1",), allow_loopback=False, network_enabled=True)
    with pytest.raises(WebDenied):
        WebFetcher(policy).get(server.url("/page"))
    assert server.requests == []


def test_credentials_in_a_url_are_refused(server):
    url = server.url("/page").replace("http://", "http://user:pass@")
    with pytest.raises(WebDenied):
        WebFetcher(loopback_policy()).get(url)


def test_non_standard_ports_are_refused_without_loopback_authorisation():
    policy = WebPolicy(allowlist=("example.com",), network_enabled=True)
    with pytest.raises(WebDenied):
        WebFetcher(policy).get("http://example.com:8080/x")


def test_non_standard_ports_are_allowed_for_authorised_loopback(server):
    """Local testing uses an ephemeral port; that is exactly what allow_loopback authorises."""
    assert WebFetcher(loopback_policy()).get(server.url("/plain"))["status"] == 200


# ---------------------------------------------------------------------------------------
# retrieval and extraction
# ---------------------------------------------------------------------------------------
def test_get_returns_text_provenance_and_no_script_content(server):
    document = WebFetcher(loopback_policy()).get(server.url("/page"))
    assert document["status"] == 200
    assert "release train leaves on Friday" in document["text"]
    assert "alert" not in document["text"]                      # script content is dropped
    assert document["title"] == "Release notes"
    assert document["trust"] == "untrusted" and document["sha256"]
    assert document["source"] == document["final_url"] == server.url("/page")
    assert document["fetched_at"] and isinstance(document["elapsed_ms"], (int, float))


def test_plain_text_and_json_are_supported(server):
    fetcher = WebFetcher(loopback_policy())
    assert "leases" in fetcher.get(server.url("/plain"))["text"]
    assert "json payload" in fetcher.get(server.url("/json"))["text"]


def test_unsupported_content_types_are_refused(server):
    with pytest.raises(WebError):
        WebFetcher(loopback_policy()).get(server.url("/binary"))


def test_404_raises_an_error_rather_than_returning_a_page(server):
    with pytest.raises(WebError):
        WebFetcher(loopback_policy()).get(server.url("/missing"))


def test_response_size_is_capped(server):
    document = WebFetcher(loopback_policy(max_bytes=1024)).get(server.url("/big"))
    assert document["truncated"] is True
    assert document["bytes"] <= 1024


def test_timeouts_are_enforced(server):
    fetcher = WebFetcher(loopback_policy(timeout=0.5))
    with pytest.raises(WebError):
        fetcher.get(server.url("/slow"))


def test_identical_requests_are_not_shared_between_callers(server):
    """No global cache: two documents are separately attributable."""
    fetcher = WebFetcher(loopback_policy())
    first = fetcher.get(server.url("/page"))
    second = fetcher.get(server.url("/page"))
    assert first["sha256"] == second["sha256"]
    assert server.requests.count("/page") == 2


# ---------------------------------------------------------------------------------------
# redirects are revalidated hop by hop
# ---------------------------------------------------------------------------------------
def test_an_allow_listed_redirect_is_followed_and_recorded(server):
    document = WebFetcher(loopback_policy()).get(server.url("/hop"))
    assert document["final_url"] == server.url("/page")
    assert document["redirects"] and document["redirects"][0]["status"] in REDIRECT_STATUSES


def test_a_redirect_off_the_allow_list_is_refused(server):
    with pytest.raises(WebDenied):
        WebFetcher(loopback_policy()).get(server.url("/offsite"))
    assert "/page" not in server.requests


def test_redirect_loops_stop_at_the_limit(server):
    with pytest.raises(WebError):
        WebFetcher(loopback_policy(max_redirects=2)).get(server.url("/loop"))
    assert len(server.requests) <= 4


# ---------------------------------------------------------------------------------------
# summarization
# ---------------------------------------------------------------------------------------
def test_summarize_is_extractive_with_sources(server):
    result = WebFetcher(loopback_policy()).summarize(server.url("/page"), max_sentences=2)
    assert result["summary"] and "release train" in result["summary"].lower()
    assert result["sources"] and result["sources"][0].startswith(server.url("/page"))
    assert result["sha256"] and result["trust"] == "untrusted"


def test_summarize_cannot_be_asked_for_more_than_the_cap(server):
    result = WebFetcher(loopback_policy()).summarize(server.url("/page"), max_sentences=500)
    assert len(result["sentences"]) <= 12


def test_extract_text_caps_output_length():
    text, _title, truncated = extract_text(b"<p>" + b"word " * 50000 + b"</p>", "text/html")
    assert len(text) <= MAX_TEXT_CHARS and truncated is True


def test_extract_text_handles_malformed_markup():
    text, _title, _truncated = extract_text(b"<p>unclosed <b>bold text", "text/html")
    assert "unclosed" in text


def test_summary_labels_content_as_data():
    """A fetched page is data. The result says so, so downstream layers cannot treat it as orders."""
    with _Server() as server:
        result = WebFetcher(loopback_policy()).summarize(server.url("/page"))
        assert result["trust"] == "untrusted"
        assert "not an instruction" in result["note"]
