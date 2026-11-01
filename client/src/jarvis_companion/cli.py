"""``jarvis-companion`` — pair, run, report, acknowledge, revoke.

Text in, text out: this is a setup and status tool, not a conversational surface. It has no prompt,
accepts no free text, and never asks the server to interpret anything.

Exit codes (stable, documented in ``client/README.md``):

0 ok · 2 usage or configuration · 3 not paired · 4 credential store unavailable
5 server unreachable · 6 authentication refused · 7 protocol or display zone · 8 notifications refused
"""

from __future__ import annotations

import argparse
import json
import sys

from . import VERSION
from .config import MAX_POLL_SECONDS
from .errors import (
    AuthRejected,
    CompanionError,
    ConfigInvalid,
    CredentialUnavailable,
    DisplayZoneMissing,
    InsecureTransport,
    NotConfigured,
    NotificationUnavailable,
    ProtocolError,
    ServerUnreachable,
)
from .service import CompanionService

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NOT_PAIRED = 3
EXIT_NO_CREDENTIAL_STORE = 4
EXIT_UNREACHABLE = 5
EXIT_AUTH = 6
EXIT_PROTOCOL = 7
EXIT_NOTIFICATIONS = 8


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jarvis-companion",
        description="J.A.R.V.I.S. Windows PC companion: local OS notifications for reminders "
                    "handed over by your own local server. No chat, no speech, no cloud.",
    )
    parser.add_argument("--state-dir", default=None,
                        help="override the state directory (default: %%LOCALAPPDATA%%\\JARVIS\\Companion)")
    parser.add_argument("--version", action="version", version=f"jarvis-companion {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    pair = sub.add_parser("pair", help="redeem a pairing code from the server console")
    pair.add_argument("--server", required=True, help="e.g. http://127.0.0.1:7420 (or https://...)")
    pair.add_argument("--code", required=True, help="the single-use pairing code")
    pair.add_argument("--label", default="Windows PC")
    pair.add_argument("--poll-seconds", type=int, default=MAX_POLL_SECONDS,
                      help=f"poll cadence in seconds (1-{MAX_POLL_SECONDS}; default "
                           f"{MAX_POLL_SECONDS})")
    pair.add_argument("--allow-insecure-http", action="store_true",
                      help="permit plaintext http:// to a host other than this machine")

    run = sub.add_parser("run", help="poll for reminders and show them as OS notifications")
    run.add_argument("--once", action="store_true", help="one poll, then exit")
    run.add_argument("--max-ticks", type=int, default=None, help="stop after N polls")

    sub.add_parser("status", help="configuration, credential, journal and server reachability")
    sub.add_parser("doctor", help="platform checks: what works here, what is unverified")

    ack = sub.add_parser("ack", help="acknowledge a reminder because you acted on it")
    ack.add_argument("delivery_id", nargs="?")
    ack.add_argument("--last", action="store_true", help="the most recent reminder shown to you")

    sub.add_parser("revoke", help="revoke this client on the server and delete the local credential")
    sub.add_parser("forget", help="delete the local credential only (server still has the client)")
    sub.add_parser("register-toast", help="Windows: register this app id for toast notifications")
    return parser


def _service(args: argparse.Namespace) -> CompanionService:
    from .service import Paths
    from .config import default_state_dir

    paths = Paths(__import__("pathlib").Path(args.state_dir)) if args.state_dir else Paths(
        default_state_dir())
    return CompanionService(paths=paths)


def _print(payload: dict) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    service = _service(args)
    try:
        return _dispatch(args, service)
    except NotConfigured as exc:
        return _fail(exc, EXIT_NOT_PAIRED)
    except CredentialUnavailable as exc:
        return _fail(exc, EXIT_NO_CREDENTIAL_STORE)
    except (InsecureTransport, ConfigInvalid) as exc:
        return _fail(exc, EXIT_USAGE)
    except AuthRejected as exc:
        return _fail(exc, EXIT_AUTH)
    except ServerUnreachable as exc:
        return _fail(exc, EXIT_UNREACHABLE)
    except (DisplayZoneMissing, ProtocolError) as exc:
        return _fail(exc, EXIT_PROTOCOL)
    except NotificationUnavailable as exc:
        return _fail(exc, EXIT_NOTIFICATIONS)
    except CompanionError as exc:  # pragma: no cover - defensive
        return _fail(exc, EXIT_PROTOCOL)
    return EXIT_OK


def _fail(exc: CompanionError, code: int) -> int:
    payload = {"ok": False, "code": exc.code, "message": str(exc)}
    if exc.detail:
        payload["detail"] = exc.detail
    if isinstance(exc, AuthRejected):
        payload["kind"] = exc.kind
    print(json.dumps(payload, indent=2, sort_keys=True), file=sys.stderr)
    return code


def _dispatch(args: argparse.Namespace, service: CompanionService) -> int:
    if args.command == "pair":
        result = service.pair(server_url=args.server, code=args.code, label=args.label,
                              poll_seconds=args.poll_seconds,
                              allow_insecure_http=args.allow_insecure_http)
        result["ok"] = True
        result["next"] = ("run `jarvis-companion run` to start polling; the token is in the "
                          "credential store, not in any file")
        _print(result)
        return EXIT_OK

    if args.command == "run":
        from .poller import Companion

        config = service.load_config()
        companion: Companion = service.companion(config=config)
        if args.once:
            report = companion.tick()
            _print({"ok": report.status == "ok", "tick": report.as_dict()})
            return EXIT_OK if report.status == "ok" else EXIT_UNREACHABLE
        report = companion.run(max_ticks=args.max_ticks)
        _print({"ok": report.stopped_reason == "max_ticks", "run": report.as_dict()})
        if report.stopped_reason in ("token_expired", "token_revoked", "scope_denied",
                                     "unauthenticated", "pairing_refused"):
            return EXIT_AUTH
        if report.stopped_reason == "display_zone_missing":
            return EXIT_PROTOCOL
        return EXIT_OK

    if args.command == "status":
        _print(service.status())
        return EXIT_OK

    if args.command == "doctor":
        _print(doctor(service))
        return EXIT_OK

    if args.command == "ack":
        if bool(args.delivery_id) == bool(args.last):
            print("give exactly one of a delivery id or --last", file=sys.stderr)
            return EXIT_USAGE
        delivery_id = args.delivery_id
        if args.last:
            record = service.journal().last_unacknowledged()
            if record is None:
                _print({"ok": False, "stage": "nothing_to_acknowledge",
                        "message": "no reminder has been shown and left unacknowledged"})
                return EXIT_NOT_PAIRED
            delivery_id = record.delivery_id
        companion = service.companion()
        result = companion.acknowledge(delivery_id)
        result["ok"] = bool(result.get("server_confirmed"))
        _print(result)
        return EXIT_OK if result.get("server_confirmed") else EXIT_UNREACHABLE

    if args.command == "revoke":
        result = service.revoke()
        result["ok"] = True
        result["note"] = "queued reminder events for this client were closed by the server"
        _print(result)
        return EXIT_OK

    if args.command == "forget":
        result = service.forget()
        result["ok"] = True
        _print(result)
        return EXIT_OK

    if args.command == "register-toast":
        from .windows_notify import WindowsToastNotifier

        _print(WindowsToastNotifier().ensure_registered())
        return EXIT_OK

    print(f"unknown command {args.command!r}", file=sys.stderr)  # pragma: no cover
    return EXIT_USAGE


def doctor(service: CompanionService) -> dict:
    """What works here, what does not, and what has never been run.

    Reported honestly: on a non-Windows host the platform checks are ``unavailable``, and on Windows
    they are ``unverified`` until the checklist in the README has actually been executed there.
    """
    checks: dict[str, dict] = {}

    try:
        store = service.credentials()
        available, why = store.available()
        store_name = store.name
    except CredentialUnavailable as exc:
        available, why, store_name = False, str(exc), "unavailable"
    checks["credential_store"] = {
        "state": "ok" if available else "unavailable",
        "detail": why,
        "store": store_name,
    }
    notifier = service.notifier()
    state, detail = notifier.availability()
    checks["os_notifications"] = {
        "state": state,  # ok | denied | unsupported
        "detail": detail,
        "api": getattr(notifier, "api", "unknown"),
    }
    try:
        config = service.load_config()
        checks["configuration"] = {"state": "ok", "detail": f"{config.server_url}, "
                                                            f"{config.poll_seconds}s cadence"}
    except CompanionError as exc:
        config = None
        checks["configuration"] = {"state": "unavailable", "detail": str(exc)}
    if config is not None:
        from .api import ServerApi

        api = ServerApi(config)
        try:
            health = api.health()
            checks["server"] = {"state": "ok",
                                "detail": f"reachable at {config.server_url}, version "
                                          f"{health.get('version')}"}
        except CompanionError as exc:
            checks["server"] = {"state": "unreachable", "detail": str(exc)}
        try:
            token = service.token()
        except CompanionError as exc:
            token = None
            checks["paired"] = {"state": "unavailable", "detail": str(exc)}
        if token:
            try:
                inbox = api.inbox(token, limit=1)
                zone = inbox.get("display_timezone")
                checks["paired"] = {"state": "ok", "detail": "credential accepted by the server"}
                checks["display_zone"] = {
                    "state": "ok" if zone else "unavailable",
                    "detail": (f"server renders reminder times in {zone}" if zone else
                               "the server published no display time zone"),
                }
            except CompanionError as exc:
                checks["paired"] = {"state": "refused", "detail": str(exc)}
    checks["windows_integration"] = {
        "state": "unverified",
        "detail": "toast delivery, Credential Manager round-trip and sleep/resume have not been "
                  "executed on a Windows host; see client/README.md section 7",
    }
    failing = [name for name, check in checks.items() if check["state"] not in ("ok", "unverified")]
    unverified = [name for name, check in checks.items() if check["state"] == "unverified"]
    return {
        "ok": not failing,
        "failing": failing,
        "unverified": unverified,
        "version": VERSION,
        "checks": checks,
        "note": ("an 'unverified' check is not a pass and is not a failure; 'ok' here means no "
                 "check failed, and it is never a statement that the client works on Windows"),
    }


__all__ = ["build_parser", "doctor", "main"]
