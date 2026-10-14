"""Command-line interface for JARVIS."""
from __future__ import annotations
import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from . import __version__
from .config import Settings
from .core import Jarvis
from .db import Store

def _print(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jarvis", description="JARVIS personal operations system")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Initialize local data store")
    sub.add_parser("status", help="Show system and task status")
    project = sub.add_parser("project", help="Manage projects").add_subparsers(dest="action", required=True)
    addp = project.add_parser("add"); addp.add_argument("name"); addp.add_argument("description", nargs="?", default="")
    task = sub.add_parser("task", help="Manage tasks").add_subparsers(dest="action", required=True)
    addt = task.add_parser("add"); addt.add_argument("title"); addt.add_argument("--project"); addt.add_argument("--description", default=""); addt.add_argument("--priority", type=int, default=3)
    listt = task.add_parser("list"); listt.add_argument("--status"); listt.add_argument("--project")
    stat = task.add_parser("status"); stat.add_argument("id", type=int); stat.add_argument("value", choices=["todo","in_progress","blocked","done","cancelled"])
    mem = sub.add_parser("memory", help="Manage persistent memory").add_subparsers(dest="action", required=True)
    savem = mem.add_parser("save"); savem.add_argument("key"); savem.add_argument("value"); savem.add_argument("--category", default="general")
    searchm = mem.add_parser("search"); searchm.add_argument("query", nargs="?", default="")
    ask = sub.add_parser("ask"); ask.add_argument("message")
    serve = sub.add_parser("serve"); serve.add_argument("--host"); serve.add_argument("--port", type=int)
    return p

def main() -> None:
    args = build_parser().parse_args()
    settings = Settings.load()
    app = Jarvis(settings)
    if args.command == "init":
        _print({"initialized": True, "version": __version__, "home": str(settings.home), "workspace": str(settings.workspace)})
    elif args.command == "status":
        _print({"version": __version__, **app.store.status(), "network_enabled": settings.network_enabled, "provider_enabled": app.provider is not None})
    elif args.command == "project":
        _print(app.store.add_project(args.name, args.description))
    elif args.command == "task":
        if args.action == "add": _print(app.store.add_task(args.title, args.project, args.description, args.priority))
        elif args.action == "list": _print(app.store.list_tasks(args.status, args.project))
        else: _print(app.store.set_task_status(args.id, args.value))
    elif args.command == "memory":
        if args.action == "save": _print(app.store.remember(args.key, args.value, args.category))
        else: _print(app.store.search_memories(args.query))
    elif args.command == "ask":
        _print(app.ask(args.message))
    elif args.command == "serve":
        host, port = args.host or settings.host, args.port or settings.port
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise SystemExit("Refusing non-loopback bind: configure authentication before exposing JARVIS.")
        handler = _handler(app)
        server = ThreadingHTTPServer((host, port), handler)
        print(f"JARVIS v{__version__} listening on http://{host}:{port}")
        try: server.serve_forever()
        except KeyboardInterrupt: pass
        finally: server.server_close()

def _handler(app: Jarvis):
    class Handler(BaseHTTPRequestHandler):
        server_version = "JARVIS/0.2"
        def log_message(self, fmt, *args):
            # Avoid default request logging of potentially sensitive URL/query content.
            return
        def _send(self, status, payload):
            raw = json.dumps(payload, ensure_ascii=False, default=str).encode()
            self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw))); self.send_header("Cache-Control", "no-store")
            self.end_headers(); self.wfile.write(raw)
        def do_GET(self):
            if self.path == "/health":
                return self._send(200, {"ok": True, "version": __version__, "mode": "local"})
            if self.path == "/v1/status":
                return self._send(200, app.store.status())
            if self.path == "/v1/projects":
                return self._send(200, app.store.list_projects())
            if self.path == "/v1/tasks":
                return self._send(200, app.store.list_tasks())
            return self._send(404, {"error": "not_found"})
        def do_POST(self):
            if self.path != "/v1/ask":
                return self._send(404, {"error": "not_found"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16384: return self._send(413, {"error": "body_size_invalid"})
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict) or not isinstance(data.get("message"), str):
                    return self._send(400, {"error": "message_required"})
                if len(data["message"]) > 8000: return self._send(413, {"error": "message_too_long"})
                # The API never auto-executes model-proposed tools.
                return self._send(200, app.ask(data["message"], execute_proposed_tool=False))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return self._send(400, {"error": "invalid_json"})
            except Exception as exc:
                return self._send(500, {"error": "internal_error", "type": type(exc).__name__})
        def do_PUT(self): return self._send(405, {"error": "method_not_allowed"})
        def do_DELETE(self): return self._send(405, {"error": "method_not_allowed"})
    return Handler

if __name__ == "__main__":
    main()
