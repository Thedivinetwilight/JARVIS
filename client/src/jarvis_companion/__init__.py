"""J.A.R.V.I.S. Windows PC companion: local OS notifications for reminder handovers.

This package is a *client*. It talks to one local J.A.R.V.I.S. server over its authenticated HTTP
API, shows due reminders through the operating system's own notification facility, and acknowledges
a reminder only when the owner acts. It contains no speech, no text conversation, no cloud client and
no second server.
"""

from __future__ import annotations

#: Reported by ``status``/``doctor``. Kept equal to the packaged version in pyproject.toml.
VERSION = "0.1.0"

#: The AppUserModelID the Windows toast backend registers. Notifications are attributed to this id,
#: so the owner can find and switch them off in Settings > System > Notifications.
APP_USER_MODEL_ID = "JARVIS.Companion"

#: The platform string this client sends at pairing time.
PLATFORM_LABEL = "windows-pc"

__all__ = ["APP_USER_MODEL_ID", "PLATFORM_LABEL", "VERSION"]
