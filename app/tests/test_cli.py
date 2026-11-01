"""The operator CLI: the only way to create an operator token, a pairing code or a revocation."""

from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from app.jarvis.cli import main
from app.tests.support import ENV, SECRET, write_config


class CLITests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.directory = pathlib.Path(self._tmp.name)
        self.config_path = write_config(self.directory)
        self._previous = dict(os.environ)
        os.environ.update(ENV)
        self.addCleanup(self._restore_env)

    def _restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self._previous)

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main([*argv, "--config", str(self.config_path)])
        return code, out.getvalue(), err.getvalue()

    def test_check_config_reports_a_public_view_without_the_secret(self):
        code, out, err = self.run_cli("check-config")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertNotIn(SECRET, out)
        self.assertEqual(payload["config"]["server"]["port"], 7420)

    def test_a_refused_configuration_exits_non_zero_without_a_traceback(self):
        os.environ["JARVIS_SERVER_SECRET"] = "short"
        code, out, err = self.run_cli("check-config")
        self.assertNotEqual(code, 0)
        self.assertIn("configuration refused", err)
        self.assertNotIn("Traceback", err)

    def test_migrate_is_idempotent_and_reported(self):
        code, out, err = self.run_cli("migrate")
        self.assertEqual(code, 0, err)
        first = json.loads(out)
        self.assertEqual(first["applied"],
                         ["0001_core.sql", "0002_tasks_reminders.sql"])
        code, out, _ = self.run_cli("migrate")
        self.assertEqual(json.loads(out)["applied"], [])

    def test_commands_refuse_to_run_before_migration(self):
        code, out, err = self.run_cli("clients")
        self.assertNotEqual(code, 0)
        self.assertIn("migrate", err.lower())

    def test_operator_then_pair_code_then_revoke(self):
        self.assertEqual(self.run_cli("migrate")[0], 0)

        code, out, err = self.run_cli("operator", "--label", "console")
        self.assertEqual(code, 0, err)
        self.assertIn("jvs_", out)
        operator_token = next(line.strip() for line in out.splitlines()
                              if line.strip().startswith("jvs_"))
        self.assertNotIn(operator_token, err)

        code, out, err = self.run_cli("pair-code", "--label-hint", "kitchen tablet",
                                      "--platform", "tablet")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertRegex(payload["pairing_code"], r"^[A-Z2-9]{4}-")

        code, out, err = self.run_cli("pair-code", "--label-hint", "x", "--platform", "tablet",
                                      "--scopes", "clients.revoke")
        self.assertNotEqual(code, 0)
        self.assertIn("scope_not_grantable", err)

        code, out, err = self.run_cli("clients")
        self.assertEqual(code, 0, err)
        clients = json.loads(out)
        self.assertEqual(len(clients), 1)
        client_id = clients[0]["id"]

        code, out, err = self.run_cli("revoke", "--client-id", client_id, "--reason", "test")
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["client"]["revoked_at"])

    def test_status_audit_and_limitations_are_machine_readable(self):
        self.assertEqual(self.run_cli("migrate")[0], 0)
        code, out, err = self.run_cli("status")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["storage"]["applied_count"], 2)
        statuses = {item["id"]: item["status"] for item in payload["manifests"]}
        self.assertEqual(statuses["speech.asr"], "unavailable")

        code, out, err = self.run_cli("limitations")
        self.assertEqual(code, 0, err)
        codes = {item["code"] for item in json.loads(out)["limitations"]}
        self.assertIn("no_model_selected", codes)

        code, out, err = self.run_cli("audit", "--limit", "5")
        self.assertEqual(code, 0, err)
        self.assertIsInstance(json.loads(out), list)

    def test_purge_audio_reports_what_it_deleted(self):
        self.assertEqual(self.run_cli("migrate")[0], 0)
        code, out, err = self.run_cli("purge-audio")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["audio_files_deleted"], 0)

    def test_serve_starts_on_a_real_socket_and_shuts_down_cleanly_on_sigterm(self):
        # A free port, taken and released: the race is acceptable for a local integration test.
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()

        config_path = write_config(self.directory, name="serve.toml", port=port,
                                   data_dir=str(self.directory / "serve-data"))
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(pathlib.Path(__file__).resolve().parents[2])
        process = subprocess.Popen(
            [sys.executable, "-m", "app.jarvis", "serve", "--config", str(config_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, text=True,
        )
        self.addCleanup(lambda: process.poll() is None and process.kill())
        try:
            deadline = time.time() + 20
            last_error = None
            while time.time() < deadline:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as reply:
                        self.assertEqual(reply.status, 200)
                        break
                except (urllib.error.URLError, ConnectionError) as exc:  # not up yet
                    last_error = exc
                    time.sleep(0.1)
            else:  # pragma: no cover - only on a broken start
                self.fail(f"server did not start: {last_error}")
            process.terminate()
            self.assertEqual(process.wait(timeout=10), 0)
        finally:
            if process.poll() is None:  # pragma: no cover - defensive
                process.kill()
            process.stdout.close()
            process.stderr.close()

    def test_unknown_arguments_are_rejected_by_the_parser(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                main(["nonsense", "--config", str(self.config_path)])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
