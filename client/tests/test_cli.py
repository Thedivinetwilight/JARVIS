"""The command line: pairing, status, doctor, run, ack, forget and revoke.

Credential Manager is replaced by the in-memory store here — the only place in the repository where
that double is used outside a unit test — because the test host is not Windows. Everything else,
including the server, is real.
"""

from __future__ import annotations

import contextlib
import io
import json
import pathlib
import tempfile
import unittest
import unittest.mock

from . import support

from jarvis_companion import cli
from jarvis_companion.credentials import InMemoryCredentialStore, credential_target


class CliBase(unittest.TestCase):
    def setUp(self) -> None:
        self.server = support.LiveServer().start()
        self.addCleanup(self.server.stop)
        self.state_dir = pathlib.Path(tempfile.mkdtemp(prefix="companion-cli-"))
        self.store = InMemoryCredentialStore()
        patcher = unittest.mock.patch("jarvis_companion.service.default_credential_store",
                                      lambda: self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

    # ------------------------------------------------------------------ helpers
    def run_cli(self, *args: str) -> tuple[int, dict, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--state-dir", str(self.state_dir), *args])
        text = out.getvalue() or err.getvalue()
        payload = json.loads(text) if text.strip().startswith("{") else {"text": text}
        return code, payload, err.getvalue()

    def pair(self, **kwargs) -> dict:
        code = kwargs.pop("code", None) or self.server.pairing_code()
        args = ["pair", "--server", self.server.url, "--code", code]
        return self.run_cli(*args, **{k: str(v) for k, v in {}.items()})[1] if False else \
            self.run_cli(*[a for a in args])[1]

    def pair_ok(self) -> dict:
        code = self.server.pairing_code()
        exit_code, payload, _ = self.run_cli("pair", "--server", self.server.url, "--code", code)
        self.assertEqual(exit_code, 0, payload)
        return payload

    @property
    def config_path(self) -> pathlib.Path:
        return self.state_dir / "companion.json"


class PairingTests(CliBase):
    def test_pairing_stores_the_token_in_the_store_and_nothing_in_the_file(self):
        payload = self.pair_ok()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["client_id"].startswith("cli_"))
        self.assertIn("reminders.receive", payload["scopes"])
        self.assertIn("credential", payload["next"])
        text = self.config_path.read_text(encoding="utf-8")
        self.assertNotIn("jvs_", text)
        self.assertNotIn("token", text.lower())
        config = json.loads(text)
        self.assertEqual(config["client_id"], payload["client_id"])
        self.assertEqual(config["poll_seconds"], 10)
        self.assertIsNotNone(self.store.get(credential_target("127.0.0.1", int(
            self.server.url.rsplit(":", 1)[1]))))

    def test_a_code_that_was_already_used_is_refused_with_exit_6(self):
        code = self.server.pairing_code()
        first = self.run_cli("pair", "--server", self.server.url, "--code", code)
        self.assertEqual(first[0], 0)
        second = self.run_cli("pair", "--server", self.server.url, "--code", code)
        self.assertEqual(second[0], cli.EXIT_AUTH)
        self.assertEqual(second[1]["kind"], "pairing_refused")

    def test_a_slower_cadence_is_refused_with_exit_2(self):
        code = self.server.pairing_code()
        exit_code, payload, _ = self.run_cli("pair", "--server", self.server.url, "--code", code,
                                             "--poll-seconds", "30")
        self.assertEqual(exit_code, cli.EXIT_USAGE)
        self.assertEqual(payload["code"], "config_invalid")
        self.assertIn("poll_seconds", payload["message"])

    def test_plaintext_http_to_another_machine_is_refused_with_exit_2(self):
        exit_code, payload, _ = self.run_cli("pair", "--server", "http://192.168.1.20:7420",
                                             "--code", "AAAA-BBBB-CCCC")
        self.assertEqual(exit_code, cli.EXIT_USAGE)
        self.assertEqual(payload["code"], "insecure_transport")

    def test_a_token_without_the_receive_scope_is_refused_after_pairing(self):
        code = self.server.pairing_code(scopes=("status.read",))
        exit_code, payload, _ = self.run_cli("pair", "--server", self.server.url, "--code", code)
        self.assertEqual(exit_code, cli.EXIT_PROTOCOL)
        self.assertIn("reminders.receive", payload["message"])
        self.assertFalse(self.config_path.exists(), "a refused pairing must not be saved")


class ReportingTests(CliBase):
    def test_status_reports_the_server_the_credential_and_the_zone(self):
        self.pair_ok()
        exit_code, payload, _ = self.run_cli("status")
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["configuration"]["paired"])
        self.assertTrue(payload["configuration"]["credential_present"])
        self.assertTrue(payload["server"]["reachable"])
        self.assertEqual(payload["server"]["display_timezone"], "UTC")

    def test_status_without_pairing_explains_what_to_run(self):
        exit_code, payload, _ = self.run_cli("status")
        self.assertEqual(exit_code, 0)
        self.assertFalse(payload["configuration"]["paired"])
        self.assertIn("jarvis-companion pair", payload["configuration"]["problem"])

    def test_doctor_never_lets_an_unverified_check_read_as_a_pass(self):
        exit_code, payload, _ = self.run_cli("doctor")
        self.assertEqual(exit_code, 0, "diagnostics ran; the exit code is not a health verdict")
        self.assertIn("windows_integration", payload["unverified"])
        self.assertNotIn("windows_integration", payload["failing"])
        self.assertIn("never a statement that the client works on Windows", payload["note"])

    def test_doctor_marks_the_windows_checks_unverified_instead_of_passing_them(self):
        exit_code, payload, _ = self.run_cli("doctor")
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["checks"]["windows_integration"]["state"], "unverified")
        self.assertIn("not been executed on a Windows host",
                      payload["checks"]["windows_integration"]["detail"])
        self.assertEqual(payload["checks"]["os_notifications"]["state"], "unsupported")
        self.assertEqual(payload["checks"]["credential_store"]["state"],
                         "ok")  # the in-memory double is "available", as in the test
        self.assertFalse(payload["ok"])


class RunTests(CliBase):
    def test_a_single_poll_on_a_host_without_notifications_claims_nothing(self):
        self.pair_ok()
        client_id = json.loads(self.config_path.read_text())["client_id"]
        self.server.remind(client_id=client_id)
        self.server.advance(seconds=3600)
        exit_code, payload, _ = self.run_cli("run", "--once")
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["tick"]["events"], 1)
        self.assertEqual(payload["tick"]["shown"], 0)
        self.assertEqual(payload["tick"]["unsupported"], 1)

    def test_an_unreachable_server_exits_5_after_one_poll(self):
        self.pair_ok()
        self.server.stop()
        exit_code, payload, _ = self.run_cli("run", "--once")
        self.assertEqual(exit_code, cli.EXIT_UNREACHABLE)
        self.assertEqual(payload["tick"]["status"], "unreachable")


class AcknowledgeTests(CliBase):
    def test_acknowledging_by_delivery_id_reaches_the_server(self):
        self.pair_ok()
        client_id = json.loads(self.config_path.read_text())["client_id"]
        self.server.remind(client_id=client_id)
        self.server.advance(seconds=3600)
        self.run_cli("run", "--once")
        delivery_id = self.server.deliveries()[0]["id"]
        exit_code, payload, _ = self.run_cli("ack", delivery_id)
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["stage"], "acknowledged")
        self.assertEqual([row["state"] for row in self.server.deliveries()], ["acknowledged"])

    def test_ack_last_with_nothing_shown_says_so(self):
        self.pair_ok()
        exit_code, payload, _ = self.run_cli("ack", "--last")
        self.assertEqual(exit_code, cli.EXIT_NOT_PAIRED)
        self.assertEqual(payload["stage"], "nothing_to_acknowledge")

    def test_ack_without_an_argument_is_a_usage_error(self):
        self.pair_ok()
        exit_code, _, err = self.run_cli("ack")
        self.assertEqual(exit_code, cli.EXIT_USAGE)
        self.assertIn("exactly one", err)


class RemovalTests(CliBase):
    def test_forget_removes_the_local_credential_and_says_the_server_still_knows_us(self):
        self.pair_ok()
        exit_code, payload, _ = self.run_cli("forget")
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["credential_removed"])
        self.assertTrue(payload["server_still_has_client"])
        self.assertIn("server console", payload["note"])
        self.assertIsNone(json.loads(self.config_path.read_text())["client_id"])

    def test_revoke_tells_the_server_first_and_then_removes_the_credential(self):
        self.pair_ok()
        client_id = json.loads(self.config_path.read_text())["client_id"]
        exit_code, payload, _ = self.run_cli("revoke")
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["server_confirmed"])
        self.assertTrue(payload["credential_removed"])
        self.assertEqual(payload["client_id"], client_id)
        self.assertIsNone(self.store.get(credential_target(
            "127.0.0.1", int(self.server.url.rsplit(":", 1)[1]))))
        revoked = [client for client in self.server.app.auth.list_clients()
                   if client.id == client_id][0]
        self.assertIsNotNone(revoked.revoked_at)

    def test_revoke_reports_the_servers_own_count_of_cancelled_deliveries(self):
        """The server calls it `reminder_deliveries_cancelled`; the client must not invent a name."""
        self.pair_ok()
        client_id = json.loads(self.config_path.read_text())["client_id"]
        self.server.remind(client_id=client_id, title="water the plants")
        exit_code, payload, _ = self.run_cli("revoke")
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["reminder_deliveries_cancelled"], 1)
        self.assertIn("token stops working immediately", payload["effect"])
        self.assertNotIn("cancelled_events", payload)

    def test_register_toast_on_this_host_exits_8_rather_than_pretending(self):
        exit_code, payload, _ = self.run_cli("register-toast")
        self.assertEqual(exit_code, cli.EXIT_NOTIFICATIONS)
        self.assertEqual(payload["code"], "notification_unavailable")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
