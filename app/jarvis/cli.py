"""Operator CLI.

Everything privileged happens here, on the server, at a console — never over HTTP:
creating the operator token, minting a single-use pairing code, listing clients, revoking
a client, reading the audit trail and doing an explicit migration. The HTTP surface has no
route that can create an operator or mint a pairing code.

Usage (from the repository root)::

    python3 -m app.jarvis check-config --config app/deploy/config.example.toml
    python3 -m app.jarvis migrate      --config /etc/jarvis/jarvis.toml
    python3 -m app.jarvis operator     --config /etc/jarvis/jarvis.toml
    python3 -m app.jarvis pair-code    --config /etc/jarvis/jarvis.toml \\
        --label-hint "kitchen tablet" --platform tablet
    python3 -m app.jarvis serve        --config /etc/jarvis/jarvis.toml
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace

from . import __version__, manifests
from .app import App
from .config import load_config
from .db import Store
from .errors import JarvisError

EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_NOT_MIGRATED = 3
EXIT_ERROR = 4


def _build_app(config, *, install_guard: bool = False) -> App:
    store = Store(config.storage.database_path)
    return App(config, store=store, install_guard=install_guard)


def _require_migrated(app: App) -> None:
    status = app.store.status()
    if not status["up_to_date"]:
        raise SystemExit(
            f"database is not migrated (pending: {', '.join(status['pending'])}). "
            "Run: python3 -m app.jarvis migrate --config <path>"
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m app.jarvis",
                                     description="J.A.R.V.I.S. local server (foundation build)")
    parser.add_argument("--version", action="version", version=f"jarvis {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        child = sub.add_parser(name, help=help_text)
        child.add_argument("--config", required=True, help="path to the TOML configuration file")
        return child

    serve = add("serve", "run the HTTP server")
    serve.add_argument("--no-migrate", action="store_true",
                       help="do not apply pending migrations (readiness will report not-ready)")
    serve.add_argument("--port", type=int, default=None, help="override the configured port")

    add("check-config", "validate configuration and exit")
    add("migrate", "apply pending database migrations")

    operator = add("operator", "create an operator token (printed once)")
    operator.add_argument("--label", default="operator")

    pair = add("pair-code", "mint a single-use pairing code for a device")
    pair.add_argument("--label-hint", required=True)
    pair.add_argument("--platform", required=True,
                      help="pc, tablet, iphone, android_phone, vr, rayban_glasses, ...")
    pair.add_argument("--scopes", default="",
                      help="comma-separated scopes; defaults to the least-privilege device set")
    pair.add_argument("--ttl-minutes", type=int, default=None)

    add("clients", "list paired clients")

    revoke = add("revoke", "revoke a client")
    revoke.add_argument("--client-id", required=True)
    revoke.add_argument("--reason", default="revoked from the server console")

    audit = add("audit", "print the most recent audit records")
    audit.add_argument("--limit", type=int, default=20)

    add("limitations", "print the limitation catalogue")
    add("purge-audio", "delete stored audio older than the retention window")
    add("status", "print storage, client and manifest status")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_config(args.config)
    except JarvisError as exc:
        print(f"configuration refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED

    if args.command == "check-config":
        print(json.dumps({"ok": True, "config": config.public_dict()}, indent=2, sort_keys=True))
        return EXIT_OK

    if args.command == "serve":
        # Delegated to ``server.serve`` so that SIGTERM/SIGINT, the egress guard and the
        # startup purge are handled in exactly one place.
        from .server import serve

        if args.port is not None:
            if not 1 <= args.port <= 65535:
                print("--port must be between 1 and 65535", file=sys.stderr)
                return EXIT_REFUSED
            config = replace(config, server=replace(config.server, port=args.port))
        try:
            return serve(config, migrate=not args.no_migrate)
        except JarvisError as exc:
            print(f"error [{exc.code}]: {exc}", file=sys.stderr)
            return EXIT_ERROR

    try:
        app = _build_app(config)
        if args.command == "migrate":
            result = app.store.migrate()
            print(json.dumps({"applied": list(result.applied), "total": result.total}, indent=2))
            return EXIT_OK

        _require_migrated(app)

        if args.command == "operator":
            client, token = app.auth.create_operator(args.label)
            print("operator created; the token is shown once and never again:")
            print(token)
            print(json.dumps({"client_id": client.id, "expires_at": client.expires_at,
                              "scopes": list(client.scopes)}, indent=2))
            return EXIT_OK

        if args.command == "pair-code":
            scopes = [item for item in args.scopes.split(",") if item] or None
            payload = app.auth.create_pairing_code(
                label_hint=args.label_hint,
                platform=args.platform,
                scopes=scopes,
                ttl_minutes=args.ttl_minutes,
            )
            print(json.dumps(payload, indent=2, sort_keys=True))
            return EXIT_OK

        if args.command == "clients":
            print(json.dumps([client.to_dict() for client in app.auth.list_clients()], indent=2,
                             sort_keys=True))
            return EXIT_OK

        if args.command == "revoke":
            client = app.auth.revoke(
                args.client_id, reason=args.reason, actor_kind="operator", actor_id="console"
            )
            deleted = app.audio.purge_client(args.client_id)
            print(json.dumps({"client": client.to_dict(), "audio_files_deleted": deleted},
                             indent=2, sort_keys=True))
            return EXIT_OK

        if args.command == "audit":
            entries = [entry.to_dict() for entry in app.audit.tail(args.limit)]
            print(json.dumps(entries, indent=2, sort_keys=True))
            return EXIT_OK

        if args.command == "limitations":
            from .reporting import limitations_payload

            print(json.dumps(limitations_payload(app), indent=2, sort_keys=True))
            return EXIT_OK

        if args.command == "purge-audio":
            removed = app.audio.purge_expired()
            print(json.dumps({"audio_files_deleted": removed,
                              "retention_days": config.storage.audio_retention_days}, indent=2))
            return EXIT_OK

        if args.command == "status":
            print(json.dumps({
                "version": app.version,
                "storage": app.store.status(),
                "clients": [c.to_dict() for c in app.auth.list_clients()],
                "manifests": [{"id": m.id, "status": m.status} for m in manifests.MANIFESTS],
                "egress": app.guard.policy()["declared_external_services"],
                "tasks": app.tasks.counts(),
            }, indent=2, sort_keys=True))
            return EXIT_OK
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_NOT_MIGRATED
    except JarvisError as exc:
        print(f"error [{exc.code}]: {exc}", file=sys.stderr)
        return EXIT_ERROR

    print(f"unknown command: {args.command}", file=sys.stderr)  # pragma: no cover
    return EXIT_ERROR
