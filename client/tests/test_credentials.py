"""Credential handling: the OS store, or an honest refusal. Never a token in a file."""

from __future__ import annotations

import pathlib
import sys
import unittest

from . import support  # noqa: F401  (sets sys.path)

from jarvis_companion.credentials import (
    InMemoryCredentialStore,
    WindowsCredentialStore,
    credential_target,
    default_credential_store,
)
from jarvis_companion.errors import CredentialUnavailable


class CredentialTests(unittest.TestCase):
    def test_one_entry_per_server(self):
        self.assertEqual(credential_target("127.0.0.1", 7420), "JARVIS/companion/127.0.0.1:7420")
        self.assertNotEqual(credential_target("127.0.0.1", 7420),
                            credential_target("jarvis.home", 7420))

    def test_the_store_protocol_round_trips_and_deletes(self):
        store = InMemoryCredentialStore()
        self.assertIsNone(store.get("t"))
        store.set("t", "jvs_secret")
        self.assertEqual(store.get("t"), "jvs_secret")
        self.assertTrue(store.delete("t"))
        self.assertIsNone(store.get("t"))
        self.assertFalse(store.delete("t"))

    @unittest.skipUnless("win32" in sys.platform, "the Windows store exists only on Windows")
    def test_the_windows_store_round_trips_a_secret(self):  # pragma: no cover - Windows only
        store = WindowsCredentialStore()
        target = credential_target("127.0.0.1", 7420) + "/test"
        try:
            store.set(target, "jvs_windows_round_trip")
            self.assertEqual(store.get(target), "jvs_windows_round_trip")
        finally:
            store.delete(target)

    def test_the_windows_store_reports_itself_unavailable_elsewhere(self):
        if "win32" in sys.platform:  # pragma: no cover - Windows host
            self.skipTest("this assertion is about non-Windows hosts")
        store = WindowsCredentialStore()
        available, why = store.available()
        self.assertFalse(available)
        self.assertIn(sys.platform, why)
        with self.assertRaises(CredentialUnavailable):
            store.set("x", "y")

    def test_the_shipped_store_refuses_rather_than_writing_a_plain_file(self):
        if "win32" in sys.platform:  # pragma: no cover - Windows host
            self.assertIsInstance(default_credential_store(), WindowsCredentialStore)
            return
        with self.assertRaises(CredentialUnavailable) as caught:
            default_credential_store()
        message = str(caught.exception)
        self.assertIn("no OS-protected credential store", message)
        self.assertIn("no plaintext fallback", message)

    def test_no_module_offers_a_file_backed_secret_store(self):
        """A file store would be a different security claim, and the owner was not told one."""
        import jarvis_companion.credentials as module

        source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        self.assertNotIn("def read_text", source)
        self.assertNotIn("open(target", source)
        self.assertEqual(module.__all__.count("InMemoryCredentialStore"), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
