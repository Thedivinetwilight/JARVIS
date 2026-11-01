"""Configuration loading and startup validation."""

from __future__ import annotations

import os
import pathlib
import stat
import tempfile
import tomllib
import unittest

from app.jarvis.clock import DISPLAY_ZONE_REQUIRED, DISPLAY_ZONE_UNKNOWN, display_zone
from app.jarvis.config import DEFAULTS, SCHEMA, load_config
from app.jarvis.errors import ConfigError, StartupRefused
from app.tests.support import DEFAULT_EXTERNAL, ENV, SECRET, write_config

REPO = pathlib.Path(__file__).resolve().parents[2]


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.directory = pathlib.Path(self._tmp.name)

    def test_valid_configuration_loads(self):
        config = load_config(write_config(self.directory), environ=dict(ENV))
        self.assertEqual(config.server.bind, "127.0.0.1")
        self.assertEqual(config.server.port, 7420)
        self.assertFalse(config.server.tls_enabled)
        self.assertTrue(config.server.is_loopback)
        self.assertEqual(config.adapters["speech_asr"], "unavailable")
        self.assertFalse(any(config.external_services.values()))
        self.assertTrue(config.storage.data_dir.is_dir())
        self.assertEqual(stat.S_IMODE(config.storage.data_dir.stat().st_mode) & 0o777, 0o700)
        # Raw voice recordings live under this root: it must be private too.
        self.assertTrue(config.storage.audio_dir.is_dir())
        self.assertEqual(stat.S_IMODE(config.storage.audio_dir.stat().st_mode) & 0o777, 0o700)

    def test_public_dict_has_no_secret(self):
        config = load_config(write_config(self.directory), environ=dict(ENV))
        rendered = repr(config.public_dict()) + repr(config)
        self.assertNotIn(SECRET, rendered)
        self.assertIn("bind", config.public_dict()["server"])

    def test_missing_secret_is_refused(self):
        with self.assertRaises(ConfigError) as caught:
            load_config(write_config(self.directory), environ={})
        self.assertIn("no server secret", str(caught.exception))

    def test_short_secret_is_refused(self):
        with self.assertRaises(ConfigError) as caught:
            load_config(write_config(self.directory),
                        environ={"JARVIS_SERVER_SECRET": "tooshort"})
        self.assertIn("at least 32 bytes", str(caught.exception))

    def test_placeholder_secret_is_refused(self):
        with self.assertRaises(ConfigError) as caught:
            load_config(write_config(self.directory),
                        environ={"JARVIS_SERVER_SECRET": "changeme-changeme-changeme-changeme"})
        self.assertIn("placeholder", str(caught.exception).lower())

    def test_secret_file_must_be_private(self):
        secret_path = self.directory / "secret.txt"
        secret_path.write_text("y" * 48, encoding="utf-8")
        secret_path.chmod(0o644)
        write_config(self.directory, name="with-file.toml",
                     data_dir=str(self.directory / "data2"))
        text = (self.directory / "with-file.toml").read_text(encoding="utf-8")
        text = text.replace('secret_file = ""', f'secret_file = "{secret_path}"')
        path = self.directory / "with-file.toml"
        path.write_text(text, encoding="utf-8")
        with self.assertRaises(ConfigError) as caught:
            load_config(path, environ={})
        self.assertIn("readable by group or others", str(caught.exception))

    def test_secret_file_with_0600_is_accepted(self):
        secret_path = self.directory / "secret.txt"
        secret_path.write_text("f3a1c9d7b25e4086a1c3e5f7092b4d6c8e0f1a3b", encoding="utf-8")
        secret_path.chmod(0o600)
        path = write_config(self.directory, name="with-file.toml",
                            data_dir=str(self.directory / "data3"))
        text = path.read_text(encoding="utf-8").replace('secret_file = ""',
                                                        f'secret_file = "{secret_path}"')
        path.write_text(text, encoding="utf-8")
        config = load_config(path, environ={})
        self.assertEqual(config.server_secret, "f3a1c9d7b25e4086a1c3e5f7092b4d6c8e0f1a3b")

    def test_unknown_section_is_refused(self):
        path = write_config(self.directory)
        path.write_text(path.read_text(encoding="utf-8") + "\n[mystery]\nkey = 1\n",
                        encoding="utf-8")
        with self.assertRaises(ConfigError) as caught:
            load_config(path, environ=dict(ENV))
        self.assertIn("unknown section", str(caught.exception))

    def test_unknown_key_is_refused(self):
        path = write_config(self.directory)
        path.write_text(path.read_text(encoding="utf-8").replace(
            "[server]", "[server]\nband = \"127.0.0.1\""), encoding="utf-8")
        with self.assertRaises(ConfigError) as caught:
            load_config(path, environ=dict(ENV))
        self.assertIn("unknown key server.band", str(caught.exception))

    def test_wrong_type_is_refused(self):
        path = write_config(self.directory)
        path.write_text(path.read_text(encoding="utf-8").replace("port = 7420", 'port = "7420"'),
                        encoding="utf-8")
        with self.assertRaises(ConfigError) as caught:
            load_config(path, environ=dict(ENV))
        self.assertIn("server.port must be int", str(caught.exception))

    def test_missing_required_key_is_refused(self):
        path = write_config(self.directory)
        path.write_text(path.read_text(encoding="utf-8").replace('bind = "127.0.0.1"\n', ''),
                        encoding="utf-8")
        with self.assertRaises(ConfigError) as caught:
            load_config(path, environ=dict(ENV))
        self.assertIn("server.bind", str(caught.exception))

    def test_non_loopback_bind_requires_tls(self):
        self.directory.joinpath("data4").mkdir()
        path = write_config(self.directory, data_dir=str(self.directory / "data4"))
        path.write_text(path.read_text(encoding="utf-8").replace('bind = "127.0.0.1"',
                                                                'bind = "0.0.0.0"'),
                        encoding="utf-8")
        with self.assertRaises(ConfigError) as caught:
            load_config(path, environ=dict(ENV))
        self.assertIn("without TLS", str(caught.exception))

    def test_tls_files_must_exist(self):
        path = write_config(self.directory, data_dir=str(self.directory / "data5"))
        text = path.read_text(encoding="utf-8").replace(
            'bind = "127.0.0.1"', 'bind = "127.0.0.1"\ntls_cert = "nope.pem"')
        path.write_text(text, encoding="utf-8")
        with self.assertRaises(ConfigError) as caught:
            load_config(path, environ=dict(ENV))
        self.assertIn("must be set together", str(caught.exception))

    def test_enabling_an_external_service_is_refused(self):
        external = DEFAULT_EXTERNAL.replace("web_retrieval = false", "web_retrieval = true")
        with self.assertRaises(StartupRefused) as caught:
            load_config(write_config(self.directory, external=external), environ=dict(ENV))
        self.assertIn("web_retrieval", str(caught.exception))
        self.assertIn("individually scoped", str(caught.exception))

    def test_selecting_a_speech_adapter_is_refused(self):
        with self.assertRaises(StartupRefused) as caught:
            load_config(write_config(self.directory, asr="some-local-model"), environ=dict(ENV))
        self.assertIn("G-O1", str(caught.exception))

    def test_data_dir_inside_eval_or_docs_is_refused(self):
        for blocked in ("eval", "docs"):
            with self.subTest(directory=blocked):
                target = REPO / blocked / "runtime-state"
                with self.assertRaises(ConfigError) as caught:
                    load_config(
                        write_config(self.directory, name=f"{blocked}.toml", data_dir=str(target)),
                        environ=dict(ENV),
                    )
                self.assertIn("must not live inside", str(caught.exception))

    def test_database_must_be_a_file_name(self):
        path = write_config(self.directory, data_dir=str(self.directory / "data6"))
        path.write_text(path.read_text(encoding="utf-8").replace(
            'database = "jarvis.sqlite3"', 'database = "../escape.sqlite3"'), encoding="utf-8")
        with self.assertRaises(ConfigError) as caught:
            load_config(path, environ=dict(ENV))
        self.assertIn("file name", str(caught.exception))

    def test_example_configurations_are_complete_and_secret_free(self):
        for name in ("config.example.toml", "config.trial.toml"):
            with self.subTest(config=name):
                path = REPO / "app" / "deploy" / name
                self.assertTrue(path.is_file())
                raw = tomllib.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(set(raw), set(SCHEMA))
                for section, keys in SCHEMA.items():
                    for key, (_type, required) in keys.items():
                        if required:
                            self.assertIn(key, raw[section], f"{name}: {section}.{key}")
                self._assert_no_inline_credentials(raw, name)

    def _assert_no_inline_credentials(self, parsed: dict, name: str) -> None:
        forbidden = {"secret", "password", "token", "api_key", "credentials",
                     "private_key", "access_key"}

        def walk(node, path=""):
            if isinstance(node, dict):
                for key, value in node.items():
                    self.assertNotIn(key.lower(), forbidden,
                                     f"{name}: credential-shaped key {path}{key}")
                    walk(value, f"{path}{key}.")
            elif isinstance(node, list):
                for item in node:
                    walk(item, path)

        walk(parsed)
        self.assertEqual(parsed["auth"]["secret_file"], "")

    # ------------------------------------------------------------------ display time zone
    def _config_without(self, line_fragment: str, *, replacement: str = ""):
        """Write a valid config, then remove or rewrite one line of the reminders table."""
        path = write_config(self.directory)
        text = path.read_text(encoding="utf-8")
        self.assertIn(line_fragment, text)
        path.write_text(text.replace(line_fragment, replacement), encoding="utf-8")
        return path

    def test_the_display_time_zone_has_no_default(self):
        # A default would silently render reminders in a zone the owner never chose.
        self.assertNotIn("display_timezone", DEFAULTS["reminders"])
        config = load_config(write_config(self.directory), environ=dict(ENV))
        self.assertEqual(config.reminders.display_timezone, "UTC")
        self.assertTrue(config.public_dict()["reminders"]["display_zone_required"])

    def test_a_missing_display_time_zone_is_refused_with_an_actionable_explanation(self):
        path = self._config_without('display_timezone = "UTC"\n')
        with self.assertRaises(ConfigError) as caught:
            load_config(path, environ=dict(ENV))
        message = str(caught.exception)
        self.assertIn("reminders.display_timezone is required", message)
        # Actionable: it says where to get the name, and that no zone is assumed.
        self.assertIn("timedatectl", message)
        self.assertIn("/usr/share/zoneinfo", message)
        self.assertEqual(message, DISPLAY_ZONE_REQUIRED)

    def test_a_config_with_no_reminders_section_is_refused_the_same_way(self):
        """Absence of the whole table is the same refusal, not an implicit fallback."""
        path = write_config(self.directory)
        text = path.read_text(encoding="utf-8")
        start = text.index("[reminders]")
        end = text.index("[", start + 1)
        path.write_text(text[:start] + text[end:], encoding="utf-8")
        with self.assertRaises(ConfigError) as caught:
            load_config(path, environ=dict(ENV))
        self.assertEqual(str(caught.exception), DISPLAY_ZONE_REQUIRED)

    def test_an_empty_display_time_zone_is_refused_with_the_same_explanation(self):
        path = self._config_without('display_timezone = "UTC"',
                                    replacement='display_timezone = ""')
        with self.assertRaises(ConfigError) as caught:
            load_config(path, environ=dict(ENV))
        # Absent and empty must not diverge: one message, one meaning.
        self.assertEqual(str(caught.exception), DISPLAY_ZONE_REQUIRED)

    def test_an_unknown_display_time_zone_is_refused(self):
        path = self._config_without('display_timezone = "UTC"',
                                    replacement='display_timezone = "Mars/Olympus_Mons"')
        with self.assertRaises(ConfigError) as caught:
            load_config(path, environ=dict(ENV))
        message = str(caught.exception)
        self.assertIn("not a known IANA time zone", message)
        self.assertIn("Mars/Olympus_Mons", message)
        self.assertEqual(message, DISPLAY_ZONE_UNKNOWN.format(name="Mars/Olympus_Mons"))

    def test_a_chosen_display_time_zone_is_accepted_and_reported(self):
        config = load_config(write_config(self.directory, reminders_tz="Asia/Kolkata"),
                             environ=dict(ENV))
        self.assertEqual(config.reminders.display_timezone, "Asia/Kolkata")
        self.assertEqual(config.public_dict()["reminders"]["display_timezone"], "Asia/Kolkata")
        # Stored instants stay UTC; only rendering uses the zone.
        self.assertEqual(display_zone(config.reminders.display_timezone).key, "Asia/Kolkata")

    # ------------------------------------------------------------------ late threshold
    def test_the_late_threshold_defaults_to_the_owners_ten_second_target(self):
        path = self._config_without("late_after_seconds = 10\n")
        config = load_config(path, environ=dict(ENV))
        self.assertEqual(config.reminders.late_after_seconds, 10)

    def test_the_late_threshold_is_configurable(self):
        config = load_config(write_config(self.directory, reminders_late_after=45),
                             environ=dict(ENV))
        self.assertEqual(config.reminders.late_after_seconds, 45)
        self.assertEqual(config.public_dict()["reminders"]["late_after_seconds"], 45)

    def test_a_negative_or_absurd_late_threshold_is_refused(self):
        for value in (-1, 3601):
            with self.subTest(value=value):
                with self.assertRaises(ConfigError) as caught:
                    load_config(write_config(self.directory, reminders_late_after=value),
                                environ=dict(ENV))
                self.assertIn("late_after_seconds", str(caught.exception))

    def test_missing_config_file_is_refused(self):
        with self.assertRaises(ConfigError):
            load_config(self.directory / "absent.toml", environ=dict(ENV))

    def test_environment_secret_is_not_written_to_the_data_dir(self):
        config = load_config(write_config(self.directory), environ=dict(ENV))
        for path in config.storage.data_dir.rglob("*"):
            if path.is_file():
                self.assertNotIn(SECRET.encode(), path.read_bytes())
        self.assertEqual(config.server_secret, SECRET)
        self.assertNotIn("JARVIS_SERVER_SECRET", os.environ)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
