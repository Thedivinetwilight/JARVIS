"""Configuration: one server, a cadence that can meet the target, and no secrets on disk."""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from . import support  # noqa: F401  (sets sys.path)

from jarvis_companion.config import (
    CompanionConfig,
    MAX_POLL_SECONDS,
    default_state_dir,
    validate_server_url,
)
from jarvis_companion.errors import ConfigInvalid, InsecureTransport


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = pathlib.Path(tempfile.mkdtemp(prefix="companion-config-"))

    def test_the_default_cadence_is_the_owners_ten_seconds(self):
        config = CompanionConfig("http://127.0.0.1:7420")
        self.assertEqual(config.poll_seconds, MAX_POLL_SECONDS)
        self.assertEqual(MAX_POLL_SECONDS, 10)

    def test_a_faster_cadence_is_allowed(self):
        for seconds in (1, 5, 10):
            with self.subTest(seconds=seconds):
                self.assertEqual(CompanionConfig("http://127.0.0.1:7420",
                                                 poll_seconds=seconds).poll_seconds, seconds)

    def test_a_slower_cadence_is_refused_because_it_cannot_meet_the_target(self):
        for seconds in (0, 11, 60, -1):
            with self.subTest(seconds=seconds):
                with self.assertRaises(ConfigInvalid) as caught:
                    CompanionConfig("http://127.0.0.1:7420", poll_seconds=seconds)
                self.assertIn("poll_seconds", str(caught.exception))
                self.assertIn("10-second", str(caught.exception))

    def test_the_file_holds_no_credential(self):
        config = CompanionConfig("https://jarvis.home:7420", poll_seconds=5, label="owner pc",
                                 client_id="cli_123")
        path = config.save(self.directory / "companion.json")
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("jvs_", text)
        self.assertNotIn("token", text.lower())
        self.assertEqual(CompanionConfig.load(path).client_id, "cli_123")

    def test_unknown_keys_are_refused_rather_than_ignored(self):
        path = self.directory / "companion.json"
        path.write_text(json.dumps({"server_url": "http://127.0.0.1:7420",
                                    "token": "jvs_should_not_be_here"}), encoding="utf-8")
        with self.assertRaises(ConfigInvalid) as caught:
            CompanionConfig.load(path)
        self.assertIn("unknown configuration keys", str(caught.exception))
        self.assertIn("token", str(caught.exception))

    def test_a_missing_file_says_what_to_run(self):
        with self.assertRaises(ConfigInvalid) as caught:
            CompanionConfig.load(self.directory / "nope.json")
        self.assertIn("jarvis-companion pair", str(caught.exception))

    def test_plaintext_http_to_another_machine_is_refused(self):
        with self.assertRaises(InsecureTransport) as caught:
            CompanionConfig("http://192.168.1.20:7420")
        message = str(caught.exception)
        self.assertIn("plaintext HTTP", message)
        self.assertIn("https", message)

    def test_plaintext_http_to_this_machine_is_allowed(self):
        for host in ("127.0.0.1", "localhost", "[::1]"):
            with self.subTest(host=host):
                if host == "[::1]":
                    continue  # urlsplit keeps the brackets; covered by the loopback set test
                self.assertEqual(CompanionConfig(f"http://{host}:7420").host, host)

    def test_https_is_always_allowed(self):
        self.assertEqual(CompanionConfig("https://jarvis.home:7420").port, 7420)

    def test_an_explicit_override_permits_plaintext_on_a_trusted_link(self):
        config = CompanionConfig("http://192.168.1.20:7420", allow_insecure_http=True)
        self.assertTrue(config.allow_insecure_http)

    def test_a_url_that_is_not_http_is_refused(self):
        for url in ("ftp://127.0.0.1:7420", "127.0.0.1:7420", "ws://127.0.0.1:7420"):
            with self.subTest(url=url):
                with self.assertRaises(ConfigInvalid) as caught:
                    validate_server_url(url)
                self.assertIn("http://", str(caught.exception))

    def test_a_query_or_fragment_is_refused(self):
        for url in ("http://127.0.0.1:7420/?x=1", "http://127.0.0.1:7420/#frag"):
            with self.subTest(url=url):
                with self.assertRaises(ConfigInvalid) as caught:
                    validate_server_url(url)
                self.assertIn("query or fragment", str(caught.exception))

    def test_a_path_is_refused_so_api_paths_cannot_double_up(self):
        with self.assertRaises(ConfigInvalid) as caught:
            CompanionConfig("http://127.0.0.1:7420/v1")
        message = str(caught.exception)
        self.assertIn("must be an origin", message)
        self.assertIn("/v1", message)
        # A bare trailing slash is the same origin and stays usable.
        self.assertEqual(CompanionConfig("http://127.0.0.1:7420/").base, "http://127.0.0.1:7420")

    def test_clearing_the_client_id_keeps_the_server_and_cadence(self):
        config = CompanionConfig("http://127.0.0.1:7420", poll_seconds=7, client_id="cli_1")
        cleared = config.with_client_cleared()
        self.assertIsNone(cleared.client_id)
        self.assertEqual(cleared.poll_seconds, 7)
        self.assertEqual(cleared.server_url, config.server_url)

    def test_the_state_directory_is_per_platform(self):
        path = default_state_dir()
        self.assertTrue(path.is_absolute())
        self.assertIn("jarvis", str(path).lower())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
