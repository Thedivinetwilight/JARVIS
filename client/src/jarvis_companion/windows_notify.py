"""Windows toast notifications, and the settings that can silently switch them off.

The backend is the OS's own notification facility — ``Windows.UI.Notifications`` through the
``winsdk`` bindings — attributed to an AppUserModelID this package registers for itself. It is *not*
a vendor push service: no server, account or network is involved, and the toast text comes from the
local server's response and nowhere else.

**Nothing in this module has been executed on Windows.** The repository's CI and the development host
are not Windows, so every claim here is design intent, reported as unverified by ``doctor``. The
Windows-specific integration checklist is in ``client/README.md`` §7.
"""

from __future__ import annotations

import sys
from xml.sax.saxutils import escape

from . import APP_USER_MODEL_ID
from .errors import NotificationUnavailable
from .notifier import NotificationResult, RenderedReminder, utc_now_iso

TOAST_TEMPLATE = """<toast activationType="foreground" scenario="reminder">
  <visual>
    <binding template="ToastGeneric">
      <text>{title}</text>
      <text>{body}</text>
    </binding>
  </visual>
  <audio silent="true"/>
</toast>"""


def build_toast_xml(reminder: RenderedReminder) -> str:
    """The toast payload. Reminder text only: no audio, no reply field, no navigation."""
    return TOAST_TEMPLATE.format(
        title=escape(reminder.title or "Reminder"),
        body=escape(reminder.body),
    )


def registry_paths() -> dict[str, str]:
    """Where Windows keeps the two switches that decide whether a toast is shown at all."""
    return {
        "global": r"Software\Microsoft\Windows\CurrentVersion\PushNotifications",
        "per_app": rf"Software\Microsoft\Windows\CurrentVersion\Notifications\Settings\{APP_USER_MODEL_ID}",
        "registration": rf"Software\Classes\AppUserModelId\{APP_USER_MODEL_ID}",
    }


class WindowsToastNotifier:
    """Raise one toast per reminder and report what the OS said about it."""

    api = "Windows.UI.Notifications (toast, AppUserModelID " + APP_USER_MODEL_ID + ")"

    def __init__(self, *, aumid: str = APP_USER_MODEL_ID) -> None:
        self.aumid = aumid

    # ------------------------------------------------------------------ availability
    def availability(self) -> tuple[str, str]:
        if sys.platform != "win32":
            return "unsupported", f"toast notifications need Windows; this host is {sys.platform}"
        try:
            from winsdk.windows.ui.notifications import ToastNotificationManager  # noqa: F401
        except Exception as exc:  # pragma: no cover - Windows-only import path
            return "unsupported", (
                "the winsdk bindings are missing, so no toast can be raised: "
                f"install them with `pip install winsdk` ({exc.__class__.__name__})"
            )
        enabled, detail = self._notifications_enabled()
        if not enabled:
            return "denied", detail
        return "ok", "Windows toast notifications are enabled for this app id"

    def _notifications_enabled(self) -> tuple[bool, str]:
        """Ask Windows whether toasts are switched on, before pretending one was raised."""
        if sys.platform != "win32":  # pragma: no cover - guarded by availability()
            return False, "not Windows"
        import winreg

        paths = registry_paths()
        for scope, path in (("global", paths["global"]), ("per_app", paths["per_app"])):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                    value, _kind = winreg.QueryValueEx(key, "Enabled" if scope == "per_app"
                                                       else "ToastEnabled")
            except FileNotFoundError:
                continue
            except OSError:  # pragma: no cover - unreadable hive
                continue
            if int(value) == 0:
                return False, (
                    "notifications are switched off in Windows Settings for this app id or for all "
                    "apps; nothing will be shown until the owner enables them"
                )
        return True, "enabled"

    def ensure_registered(self) -> dict:
        """Create the per-app AUMID registration so the toast is attributable and toggleable.

        A Start-menu shortcut carrying the same AppUserModelID is created by ``install.ps1``: Windows
        uses it for the display name and icon. Both steps are Windows-only and unverified here.
        """
        if sys.platform != "win32":
            raise NotificationUnavailable(
                f"registering an AppUserModelID needs Windows; this host is {sys.platform}",
                detail={"platform": sys.platform},
            )
        import winreg  # pragma: no cover - Windows-only

        path = registry_paths()["registration"]
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_WRITE) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "J.A.R.V.I.S. Companion")
        return {"app_user_model_id": self.aumid, "registry_path": f"HKCU\\{path}",
                "note": "the Start-menu shortcut with the same app id is created by install.ps1"}

    # ------------------------------------------------------------------ showing
    def show(self, reminder: RenderedReminder) -> NotificationResult:
        state, detail = self.availability()
        if state == "unsupported":
            return NotificationResult(verdict="unsupported", message=detail, api=self.api)
        if state == "denied":
            return NotificationResult(verdict="denied", message=detail, api=self.api)
        # pragma: no cover - the remainder runs only on Windows
        try:
            from winsdk.windows.data.xml.dom import XmlDocument
            from winsdk.windows.ui.notifications import (
                ToastNotification,
                ToastNotificationManager,
            )

            document = XmlDocument()
            document.load_xml(build_toast_xml(reminder))
            notifier = ToastNotificationManager.create_toast_notifier(self.aumid)
            notifier.show(ToastNotification(document))
        except Exception as exc:
            return NotificationResult(
                verdict="failed",
                message=f"the OS notification API raised {exc.__class__.__name__}; nothing was shown",
                api=self.api,
            )
        return NotificationResult(
            verdict="accepted",
            message="accepted by the OS notification API; whether a toast was drawn is the OS's "
                    "business and is not claimed",
            api=self.api,
            at=utc_now_iso(),
        )


POWERSHELL_TOAST = """
$ErrorActionPreference = 'Stop'
$payload = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{payload}'))
$document = New-Object Windows.Data.Xml.Dom.XmlDocument
$document.LoadXml($payload)
$toast = New-Object Windows.UI.Notifications.ToastNotification $document
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{aumid}')
$notifier.Show($toast)
"""


class PowerShellToastNotifier:
    """Raise the toast through PowerShell's WinRT projection.

    Used when the ``winsdk`` bindings are absent, so a stock Windows installation can still show a
    reminder without installing anything. The script travels on **stdin** (never the command line,
    which other processes can read), and the toast text is never logged.

    Unverified on Windows, like the primary backend: what is known here is that the script is built
    from escaped text, that a non-zero exit is treated as failure, and that no network is used.
    """

    api = "Windows.UI.Notifications via PowerShell (AppUserModelID " + APP_USER_MODEL_ID + ")"

    def __init__(self, *, aumid: str = APP_USER_MODEL_ID,
                 executable: str | None = None) -> None:
        self.aumid = aumid
        self.executable = executable

    def resolve_executable(self) -> str | None:
        """``powershell.exe`` on a normal Windows host; ``None`` when there is none."""
        if self.executable:
            return self.executable
        if sys.platform != "win32":  # pragma: no cover - exercised by the off-Windows tests
            import shutil

            return shutil.which("powershell") or shutil.which("pwsh")
        import shutil

        return shutil.which("powershell") or shutil.which("pwsh")

    def availability(self) -> tuple[str, str]:
        if sys.platform != "win32":
            return "unsupported", f"PowerShell toast notifications need Windows; this host is {sys.platform}"
        if not self.resolve_executable():  # pragma: no cover - Windows path
            return "unsupported", "no PowerShell host (powershell.exe or pwsh) was found"
        enabled, detail = WindowsToastNotifier(aumid=self.aumid)._notifications_enabled()
        if not enabled:  # pragma: no cover - Windows path
            return "denied", detail
        return "ok", "PowerShell can raise toasts and notifications are enabled"  # pragma: no cover

    def script_for(self, reminder: RenderedReminder) -> str:
        import base64

        encoded = base64.b64encode(build_toast_xml(reminder).encode("utf-8")).decode("ascii")
        return POWERSHELL_TOAST.format(payload=encoded, aumid=self.aumid)

    def show(self, reminder: RenderedReminder) -> NotificationResult:
        executable = self.resolve_executable()
        if not executable:  # pragma: no cover - Windows path
            return NotificationResult(
                verdict="unsupported",
                message="no PowerShell host was found, so the toast fallback cannot be used",
                api=self.api,
            )
        import subprocess  # pragma: no cover - Windows path

        try:  # pragma: no cover - Windows path
            completed = subprocess.run(
                [executable, "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", "-"],
                input=self.script_for(reminder), text=True, capture_output=True, timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - Windows path
            return NotificationResult(
                verdict="failed",
                message=f"the PowerShell notification host failed: {exc.__class__.__name__}",
                api=self.api,
            )
        if completed.returncode != 0:  # pragma: no cover - Windows path
            detail = (completed.stderr or "").strip().splitlines()
            first = detail[0][:200] if detail else "no error text"
            return NotificationResult(
                verdict="failed",
                message=f"the OS refused the toast (PowerShell exit {completed.returncode}): {first}",
                api=self.api,
            )
        return NotificationResult(  # pragma: no cover - Windows path
            verdict="accepted",
            message="accepted by the OS notification API through PowerShell; whether a toast was "
                    "drawn is the OS's business and is not claimed",
            api=self.api,
            at=utc_now_iso(),
        )


class AutoToastNotifier:
    """Prefer the winsdk backend; fall back to PowerShell; report which one is in use."""

    def __init__(self) -> None:
        self._backend = None

    def _choose(self):
        if self._backend is not None:  # pragma: no cover - cached
            return self._backend
        primary = WindowsToastNotifier()
        state, _detail = primary.availability()
        if state in ("ok", "denied"):  # pragma: no cover - Windows path
            self._backend = primary
        else:  # pragma: no cover - Windows path without winsdk
            self._backend = PowerShellToastNotifier()
        return self._backend

    @property
    def api(self) -> str:  # pragma: no cover - trivial
        return getattr(self._choose(), "api", WindowsToastNotifier.api)

    def availability(self) -> tuple[str, str]:  # pragma: no cover - Windows path
        return self._choose().availability()

    def show(self, reminder: RenderedReminder) -> NotificationResult:  # pragma: no cover
        return self._choose().show(reminder)


def platform_notifier() -> WindowsToastNotifier | None:
    """The backend for this platform, or ``None`` when there is none (tests use a double)."""
    if sys.platform == "win32":
        return WindowsToastNotifier()
    return None


__all__ = [
    "AutoToastNotifier",
    "POWERSHELL_TOAST",
    "PowerShellToastNotifier",
    "WindowsToastNotifier",
    "build_toast_xml",
    "platform_notifier",
    "registry_paths",
]
