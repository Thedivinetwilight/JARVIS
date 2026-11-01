"""The notification boundary: what a reminder looks like, and how verdicts are reported."""

from __future__ import annotations

import unittest

from . import support  # noqa: F401  (sets sys.path)

from jarvis_companion.notifier import NullNotifier, RecordingNotifier, RenderedReminder
from jarvis_companion.windows_notify import (
    APP_USER_MODEL_ID,
    WindowsToastNotifier,
    build_toast_xml,
    platform_notifier,
    registry_paths,
)


def reminder(**overrides) -> RenderedReminder:
    base = dict(title="buy milk", fire_at_local_iso="2026-11-02T09:00:00+05:30",
                display_timezone="Asia/Kolkata", delivery_id="dlv_1", reminder_id="rmd_1",
                late=False, late_by_seconds=0.0)
    base.update(overrides)
    return RenderedReminder(**base)


class RenderedReminderTests(unittest.TestCase):
    def test_the_body_carries_the_servers_time_and_zone(self):
        body = reminder().body
        self.assertIn("2026-11-02T09:00:00+05:30", body)
        self.assertIn("Asia/Kolkata", body)
        self.assertIn("Not spoken", body)
        self.assertNotIn("late by", body)

    def test_a_late_hand_over_says_by_how_much(self):
        body = reminder(late=True, late_by_seconds=10.5).body
        self.assertIn("10.500 s after the scheduled time", body)

    def test_the_body_offers_no_reply_and_no_conversation(self):
        body = reminder().body.lower()
        for forbidden in ("reply to", "type", "prompt", "chat", "ask jarvis"):
            self.assertNotIn(forbidden, body)


class ToastXmlTests(unittest.TestCase):
    def test_the_toast_is_title_and_time_only_and_silent(self):
        xml = build_toast_xml(reminder())
        self.assertIn('<text>buy milk</text>', xml)
        self.assertIn('<audio silent="true"/>', xml)
        self.assertNotIn("actions", xml)
        self.assertNotIn("input", xml)

    def test_reminder_text_cannot_inject_xml(self):
        xml = build_toast_xml(reminder(title='<b>milk</b> & "eggs"'))
        self.assertNotIn("<b>", xml)
        self.assertIn("&lt;b&gt;milk&lt;/b&gt; &amp; \"eggs\"", xml)


class WindowsBackendTests(unittest.TestCase):
    def test_off_windows_the_backend_reports_unsupported_and_shows_nothing(self):
        import sys

        if sys.platform == "win32":  # pragma: no cover - Windows host
            self.skipTest("only meaningful off Windows")
        notifier = WindowsToastNotifier()
        state, detail = notifier.availability()
        self.assertEqual(state, "unsupported")
        self.assertIn(sys.platform, detail)
        result = notifier.show(reminder())
        self.assertEqual(result.verdict, "unsupported")
        self.assertFalse(result.accepted)
        self.assertIsNone(platform_notifier())

    def test_registering_the_app_id_refuses_off_windows(self):
        import sys

        if sys.platform == "win32":  # pragma: no cover - Windows host
            self.skipTest("only meaningful off Windows")
        from jarvis_companion.errors import NotificationUnavailable

        with self.assertRaises(NotificationUnavailable):
            WindowsToastNotifier().ensure_registered()

    def test_the_registry_paths_name_this_app_id(self):
        paths = registry_paths()
        self.assertIn(APP_USER_MODEL_ID, paths["per_app"])
        self.assertIn(APP_USER_MODEL_ID, paths["registration"])
        self.assertIn("PushNotifications", paths["global"])


class PowerShellFallbackTests(unittest.TestCase):
    def test_the_fallback_script_carries_the_toast_as_base64_on_stdin_material(self):
        notifier = WindowsToastNotifier()
        self.assertFalse(notifier.availability()[0] == "ok")
        from jarvis_companion.windows_notify import PowerShellToastNotifier

        fallback = PowerShellToastNotifier()
        script = fallback.script_for(reminder())
        self.assertIn("FromBase64String", script)
        self.assertIn("ToastNotificationManager", script)
        self.assertIn(APP_USER_MODEL_ID, script)
        self.assertNotIn("buy milk", script, "the reminder text must not appear literally")

    def test_the_fallback_reports_unsupported_off_windows(self):
        import sys

        if sys.platform == "win32":  # pragma: no cover - Windows host
            self.skipTest("only meaningful off Windows")
        from jarvis_companion.windows_notify import PowerShellToastNotifier

        state, detail = PowerShellToastNotifier().availability()
        self.assertEqual(state, "unsupported")
        self.assertIn(sys.platform, detail)
        self.assertEqual(PowerShellToastNotifier().show(reminder()).verdict, "unsupported")

    def test_the_auto_backend_picks_something_reportable_off_windows(self):
        import sys

        if sys.platform == "win32":  # pragma: no cover - Windows host
            self.skipTest("only meaningful off Windows")
        from jarvis_companion.windows_notify import AutoToastNotifier

        backend = AutoToastNotifier()
        self.assertTrue(backend.api.startswith("Windows.UI.Notifications"))
        state, _detail = backend.availability()
        self.assertEqual(state, "unsupported")
        self.assertEqual(backend.show(reminder()).verdict, "unsupported")


class DoubleTests(unittest.TestCase):
    def test_the_null_backend_never_claims_success(self):
        result = NullNotifier().show(reminder())
        self.assertEqual(result.verdict, "unsupported")
        self.assertFalse(result.accepted)

    def test_the_recording_double_returns_its_scripted_verdict(self):
        double = RecordingNotifier("denied", availability=("denied", "switched off"))
        self.assertEqual(double.availability(), ("denied", "switched off"))
        result = double.show(reminder())
        self.assertEqual(result.verdict, "denied")
        self.assertFalse(result.accepted)
        self.assertEqual(len(double.shown), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
