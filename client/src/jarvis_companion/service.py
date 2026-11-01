"""Wiring: paths, credential, server client, journal, notifier — one place, no globals.

Everything the command line does goes through :class:`CompanionService`, so the CLI is thin and the
behaviour is testable without a console.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from . import PLATFORM_LABEL, VERSION
from .api import REQUIRED_SCOPE, ServerApi
from .config import CompanionConfig, default_state_dir
from .credentials import CredentialStore, credential_target, default_credential_store
from .errors import CredentialUnavailable, NotConfigured, ProtocolError
from .notifier import Notifier, NullNotifier
from .poller import Companion
from .state import Journal


@dataclass
class Paths:
    state_dir: Path

    @property
    def config(self) -> Path:
        return self.state_dir / "companion.json"

    @property
    def journal(self) -> Path:
        return self.state_dir / "journal.sqlite3"

    def create(self) -> "Paths":
        self.state_dir.mkdir(parents=True, exist_ok=True)
        return self


class CompanionService:
    """One paired client, one server, one journal."""

    def __init__(self, *, paths: Paths | None = None,
                 credentials: CredentialStore | None = None,
                 notifier: Notifier | None = None) -> None:
        self.paths = (paths or Paths(default_state_dir())).create()
        self._credentials = credentials
        self._notifier = notifier
        self._journal: Journal | None = None

    # ------------------------------------------------------------------ components
    def credentials(self) -> CredentialStore:
        if self._credentials is None:
            self._credentials = default_credential_store()
        return self._credentials

    def notifier(self) -> Notifier:
        """The OS notifier, or an explicit "unsupported" one — never a silent substitute."""
        if self._notifier is not None:
            return self._notifier
        from .windows_notify import AutoToastNotifier, platform_notifier

        if sys.platform == "win32":
            # winsdk if it is installed, PowerShell's WinRT projection otherwise.
            self._notifier = AutoToastNotifier()
        else:
            backend = platform_notifier()  # None off Windows, by design
            self._notifier = backend or NullNotifier(
                f"this is the Windows companion; no OS notification backend exists on {sys.platform}"
            )
        return self._notifier

    def journal(self) -> Journal:
        if self._journal is None:
            self._journal = Journal(self.paths.journal)
        return self._journal

    def close(self) -> None:
        if self._journal is not None:
            self._journal.close()
            self._journal = None

    def __enter__(self) -> "CompanionService":  # pragma: no cover - trivial
        return self

    def __exit__(self, *exc) -> None:  # pragma: no cover - trivial
        self.close()

    # ------------------------------------------------------------------ configuration
    def load_config(self) -> CompanionConfig:
        return CompanionConfig.load(self.paths.config)

    def save_config(self, config: CompanionConfig) -> CompanionConfig:
        config.save(self.paths.config)
        return config

    @property
    def credential_target(self) -> str:
        config = self.load_config()
        return credential_target(config.host, config.port)

    def token(self) -> str:
        """The stored token, or a refusal. Never printed, never written anywhere else."""
        config = self.load_config()
        if not config.client_id:
            raise NotConfigured(
                "this PC is not paired with a server yet; run `jarvis-companion pair` with a code "
                "from the server console",
                detail={"state_dir": str(self.paths.state_dir)},
            )
        token = self.credentials().get(credential_target(config.host, config.port))
        if not token:
            raise NotConfigured(
                "no device credential is stored for this server; pair again (the old token may have "
                "been removed with `forget`)",
                detail={"store": self.credentials().name},
            )
        return token

    # ------------------------------------------------------------------ pairing
    def pair(self, *, server_url: str, code: str, label: str | None = None,
             poll_seconds: int = 10, allow_insecure_http: bool = False) -> dict:
        config = CompanionConfig(server_url=server_url, poll_seconds=poll_seconds,
                                 label=label or "Windows PC",
                                 allow_insecure_http=allow_insecure_http)
        store = self.credentials()
        available, why = store.available()
        if not available:
            raise CredentialUnavailable(
                f"cannot store a device credential: {why}", detail={"store": store.name}
            )
        api = ServerApi(config)
        payload = api.pair(code=code, label=config.label, platform=PLATFORM_LABEL)
        client = payload["client"]
        scopes = list(client.get("scopes") or [])
        token = str(payload["token"])
        if REQUIRED_SCOPE not in scopes:
            raise ProtocolError(
                f"the server paired this client without {REQUIRED_SCOPE!r}, so it could not receive "
                "reminders. Ask the operator to mint the code with "
                f"`--scopes {REQUIRED_SCOPE}`",
                detail={"granted_scopes": scopes, "required_scope": REQUIRED_SCOPE},
            )
        store.set(credential_target(config.host, config.port), token)
        saved = config.with_client(str(client["id"]), label=str(client.get("label") or config.label))
        self.save_config(saved)
        journal = self.journal()
        journal.set_meta("client_id", str(client["id"]))
        journal.set_meta("paired_at", str(payload.get("client", {}).get("created_at") or ""))
        journal.set_meta("scopes", ",".join(scopes))
        return {
            "client_id": client["id"],
            "label": client["label"],
            "platform": client["platform"],
            "scopes": scopes,
            "credential_store": store.name,
            "state_dir": str(self.paths.state_dir),
            "version": VERSION,
        }

    # ------------------------------------------------------------------ self-revoke / forget
    def revoke(self) -> dict:
        """Ask the server to revoke this client, then delete the local credential."""
        config = self.load_config()
        client_id = config.client_id
        if not client_id:
            raise NotConfigured("this PC is not paired, so there is nothing to revoke")
        token = self.token()
        payload = ServerApi(config).revoke_self(token, client_id)
        removed = self.credentials().delete(credential_target(config.host, config.port))
        self.journal().set_meta("revoked_at", str(payload.get("client", {}).get("revoked_at") or ""))
        self.save_config(config.with_client_cleared())
        return {
            "client_id": client_id,
            "server_confirmed": True,
            "credential_removed": removed,
            # The server's key is `reminder_deliveries_cancelled`; report it verbatim rather than
            # guessing at a friendlier name and silently reporting `None`.
            "reminder_deliveries_cancelled": payload.get("reminder_deliveries_cancelled"),
            "effect": payload.get("effect"),
        }

    def forget(self) -> dict:
        """Delete the local credential only. The server still knows this client until revoked."""
        config = self.load_config()
        target = credential_target(config.host, config.port)
        removed = self.credentials().delete(target)
        self.save_config(config.with_client_cleared())
        return {
            "credential_removed": removed,
            "server_still_has_client": bool(config.client_id),
            "note": "the server was not contacted; revoke this client on the server console if the "
                    "token should stop working",
        }

    # ------------------------------------------------------------------ running
    def companion(self, *, config: CompanionConfig | None = None,
                  api: ServerApi | None = None) -> Companion:
        config = config or self.load_config()
        return Companion(
            config=config,
            api=api or ServerApi(config),
            token=self.token(),
            journal=self.journal(),
            notifier=self.notifier(),
        )

    # ------------------------------------------------------------------ reporting
    def status(self, *, check_server: bool = True) -> dict:
        report: dict = {"version": VERSION, "state_dir": str(self.paths.state_dir)}
        try:
            config = self.load_config()
        except Exception as exc:
            report["configuration"] = {"paired": False, "problem": str(exc)}
            return report
        try:
            store = self.credentials()
            available, why = store.available()
            store_name = store.name
        except CredentialUnavailable as exc:
            store, available, why, store_name = None, False, str(exc), "unavailable"
        token = None
        if available and store is not None:
            try:
                token = store.get(credential_target(config.host, config.port))
            except CredentialUnavailable as exc:  # pragma: no cover - Windows failure path
                report["credential_error"] = str(exc)
        report["configuration"] = {
            "paired": bool(config.client_id),
            "server_url": config.server_url,
            "poll_seconds": config.poll_seconds,
            "label": config.label,
            "client_id": config.client_id,
            "credential_store": store_name,
            "credential_store_available": available,
            "credential_store_detail": why,
            "credential_present": bool(token),
        }
        state, detail = self.notifier().availability()
        report["notifications"] = {"state": state, "detail": detail,
                                   "api": getattr(self.notifier(), "api", "unknown")}
        journal = self.journal()
        report["journal"] = {**journal.counts(), "last_poll_at": journal.get_meta("last_poll_at")}
        report["recent"] = [record.__dict__ for record in journal.recent(limit=5)]
        if check_server:
            api = ServerApi(config)
            try:
                health = api.health()
                report["server"] = {"reachable": True, "version": health.get("version"),
                                    "status": health.get("status")}
            except Exception as exc:
                report["server"] = {"reachable": False, "detail": str(exc)}
            if token:
                try:
                    inbox = api.inbox(token, limit=1)
                    report["server"]["display_timezone"] = inbox.get("display_timezone")
                    report["server"]["inbox_reachable"] = True
                except Exception as exc:
                    report["server"]["inbox_reachable"] = False
                    report["server"]["inbox_detail"] = str(exc)
        return report


__all__ = ["CompanionService", "Paths"]
