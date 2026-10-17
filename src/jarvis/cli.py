"""Command-line interface for JARVIS.

v0.2 commands are unchanged (init, status, project, task, memory save/search, ask,
serve). Genesis 0.1 adds the companion surface (chat, resume, goal, trace, ops,
approve/deny, capabilities, identity, state, doctor, eval) and memory controls
(correct, history, forget, conflicts, export).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .api import make_handler
from .config import ConfigError, Settings
from .contracts import ActionRequest, Origin, to_dict
from .core import Jarvis
from .util import file_mode, make_private

def _handler(app: Jarvis):
    """v0.2 name kept for compatibility; the handler is always token-protected."""
    from .api import load_or_create_token
    return make_handler(app, token=load_or_create_token(app.settings.home)[0])


def _print(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jarvis", description="JARVIS persistent personal cognitive system")
    p.add_argument("--version", action="version", version=f"jarvis {__version__}")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Initialize local data store")
    sub.add_parser("status", help="Show system and task status")
    project = sub.add_parser("project", help="Manage projects").add_subparsers(dest="action", required=True)
    addp = project.add_parser("add"); addp.add_argument("name"); addp.add_argument("description", nargs="?", default="")
    project.add_parser("list")
    task = sub.add_parser("task", help="Manage tasks").add_subparsers(dest="action", required=True)
    addt = task.add_parser("add"); addt.add_argument("title"); addt.add_argument("--project"); addt.add_argument("--description", default=""); addt.add_argument("--priority", type=int, default=3); addt.add_argument("--goal", type=int)
    listt = task.add_parser("list"); listt.add_argument("--status"); listt.add_argument("--project"); listt.add_argument("--goal", type=int)
    stat = task.add_parser("status"); stat.add_argument("id", type=int); stat.add_argument("value", choices=["todo", "in_progress", "blocked", "done", "cancelled"]); stat.add_argument("--reason", default="")
    dep = task.add_parser("depend", help="Declare that a task depends on another"); dep.add_argument("id", type=int); dep.add_argument("--on", type=int, required=True)
    blk = task.add_parser("block", help="Mark a task blocked with a reason"); blk.add_argument("id", type=int); blk.add_argument("reason")
    goal = sub.add_parser("goal", help="Manage goals").add_subparsers(dest="action", required=True)
    addg = goal.add_parser("add"); addg.add_argument("title"); addg.add_argument("--steps", default="", help="Steps separated by ';'"); addg.add_argument("--project"); addg.add_argument("--criteria", default=""); addg.add_argument("--priority", type=int, default=3)
    goal.add_parser("list")
    showg = goal.add_parser("show"); showg.add_argument("id", type=int)
    abang = goal.add_parser("abandon"); abang.add_argument("id", type=int); abang.add_argument("--reason", default="")
    mem = sub.add_parser("memory", help="Manage persistent memory").add_subparsers(dest="action", required=True)
    savem = mem.add_parser("save"); savem.add_argument("key"); savem.add_argument("value"); savem.add_argument("--category", default="general")
    searchm = mem.add_parser("search"); searchm.add_argument("query", nargs="?", default="")
    corr = mem.add_parser("correct"); corr.add_argument("id", type=int); corr.add_argument("value"); corr.add_argument("--reason", default="user correction")
    hist = mem.add_parser("history"); hist.add_argument("id", type=int)
    forg = mem.add_parser("forget", help="Irreversibly erase a memory (requires --yes)"); forg.add_argument("id", type=int); forg.add_argument("--yes", action="store_true")
    mem.add_parser("conflicts")
    res = mem.add_parser("resolve", help="Resolve a memory conflict"); res.add_argument("conflict_id", type=int); res.add_argument("--keep", type=int, required=True); res.add_argument("--reason", default="")
    exp = mem.add_parser("export", help="Export memories, goals and tasks as JSON"); exp.add_argument("--out")
    ask = sub.add_parser("ask", help="Run one cognitive cycle"); ask.add_argument("message"); ask.add_argument("--text", action="store_true", help="Print only the answer")
    sub.add_parser("chat", help="Interactive conversation (type /help)")
    voicep = sub.add_parser("voice", help="Spoken conversation via Termux:API (Android)")
    voicep.add_argument("--max-turns", type=int, default=None)
    sub.add_parser("resume", help="Where were we? (unfinished work, pending approvals, last session)")
    trace = sub.add_parser("trace", help="Show a cognitive-cycle trace (default: last)"); trace.add_argument("cycle_id", nargs="?")
    ev = sub.add_parser("events", help="Recent experience-log events"); ev.add_argument("--limit", type=int, default=30); ev.add_argument("--include-sensitive", action="store_true")
    ops = sub.add_parser("ops", help="List operations"); ops.add_argument("--status")
    appr = sub.add_parser("approve", help="Approve a proposed operation"); appr.add_argument("id", type=int)
    deny = sub.add_parser("deny", help="Deny a proposed operation"); deny.add_argument("id", type=int)
    sub.add_parser("capabilities", help="What JARVIS can and cannot do right now")
    sub.add_parser("identity", help="Persistent identity and runtime history")
    sub.add_parser("state", help="Operational state for debugging")
    sub.add_parser("doctor", help="Integrity, schema, backups and configuration checks")
    evalp = sub.add_parser("eval", help="Run fixed evaluations").add_subparsers(dest="action", required=True)
    er = evalp.add_parser("retrieval"); er.add_argument("--fixture", default="evals/retrieval_v1.json"); er.add_argument("--out")
    serve = sub.add_parser("serve"); serve.add_argument("--host"); serve.add_argument("--port", type=int)
    return p


def _chat(app: Jarvis) -> None:
    print(f"JARVIS {__version__} — persistent session. Type /help for commands, /quit to leave.")
    if app.recovery.get("interrupted_operations"):
        print(f"(Recovered after an interruption: operations {app.recovery['interrupted_operations']} have unknown "
              "outcomes and were not retried.)")
    session_id = None
    while True:
        try:
            line = input("you › ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        line = line.strip()
        if not line:
            continue
        if line in {"/quit", "/exit"}:
            break
        if line == "/help":
            print("Talk normally, or use: remember that …, idea: …, goal: … ; steps: a; b, add task …, done with task N, "
                  "what do you know about …?, where were we?, approve N / deny N, forget #N.\n"
                  "Slash commands: /state /trace /resume /capabilities /quit")
            continue
        if line == "/state":
            _print(app.state()); continue
        if line == "/trace":
            cid = app.store.last_cycle_id(); _print(app.store.cycle(cid) if cid else None); continue
        if line == "/resume":
            line = "where were we?"
        if line == "/capabilities":
            line = "what can you do?"
        try:
            result = app.ask(line, channel="repl", session_id=session_id)
        except ValueError as exc:
            print(f"jarvis › {exc}")
            continue
        session_id = result["session_id"]
        print(f"jarvis › {result['answer']}")
    if session_id:
        app.store.state.end_session(session_id, "user_exit")
    print("Session saved. Say \"where were we?\" next time to pick up.")


def _export(app: Jarvis) -> dict[str, Any]:
    return {"format": "jarvis-export-v1", "version": __version__, "identity": app.identity,
            "projects": app.store.list_projects(), "memories": app.store.memory.export(),
            "goals": [app.store.goals.progress(g["id"]) for g in
                      app.store.goals.list_goals(("proposed", "active", "completed", "abandoned"))],
            "tasks": app.store.list_tasks(), "preferences": app.store.memory.preferences()}


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        settings = Settings.load()
    except ConfigError as exc:
        raise SystemExit(f"Configuration error: {exc}") from None
    if args.command == "serve":
        host, port = args.host or settings.host, args.port or settings.port
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise SystemExit("Refusing non-loopback bind: the API has no TLS and must stay on this device.")
    if args.command == "eval":
        from .evaluation import run_retrieval_eval
        report = run_retrieval_eval(Path(args.fixture))
        if args.out:
            Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        _print(report["summary"])
        return
    app = Jarvis(settings)
    try:
        _dispatch(app, args, settings)
    finally:
        app.close()


def _dispatch(app: Jarvis, args: argparse.Namespace, settings: Settings) -> None:
    store = app.store
    if args.command == "init":
        _print({"initialized": True, "version": __version__, "home": str(settings.home), "workspace": str(settings.workspace),
                "system_id": app.identity["system_id"], "schema_version": store.schema_version})
    elif args.command == "status":
        _print({"version": __version__, **store.status(), "network_enabled": settings.network_enabled,
                "provider_enabled": app.provider is not None, "autonomy_level": settings.autonomy_level,
                "system_id": app.identity["system_id"]})
    elif args.command == "project":
        _print(store.add_project(args.name, args.description) if args.action == "add" else store.list_projects())
    elif args.command == "task":
        if args.action == "add":
            if args.goal:
                _print(store.goals.add_task(args.title, goal_id=args.goal, project=args.project,
                                            description=args.description, priority=args.priority))
            else:
                _print(store.add_task(args.title, args.project, args.description, args.priority))
        elif args.action == "list":
            _print(store.goals.list_tasks(status=args.status, project=args.project, goal_id=args.goal))
        elif args.action == "depend":
            store.goals.add_dependency(args.id, args.on)
            _print(store.goals.get_task(args.id) | {"depends_on_added": args.on})
        elif args.action == "block":
            _print(store.goals.set_task_status(args.id, "blocked", actor="user", blocker=args.reason, reason=args.reason))
        else:
            _print(store.goals.set_task_status(args.id, args.value, actor="user", reason=args.reason,
                                               evidence={"type": "user_confirmation", "event_id": None}))
    elif args.command == "goal":
        if args.action == "add":
            steps = [s.strip() for s in args.steps.split(";") if s.strip()]
            goal = store.goals.create_goal(args.title, steps=steps, project=args.project,
                                           success_criteria=args.criteria, priority=args.priority)
            store.state.set("focus.goal_id", goal["id"])
            _print(store.goals.progress(goal["id"]))
        elif args.action == "list":
            _print([store.goals.progress(g["id"]) for g in store.goals.list_goals(("active", "proposed"))])
        elif args.action == "abandon":
            _print(store.goals.abandon_goal(args.id, reason=args.reason))
        else:
            _print(store.goals.progress(args.id))
    elif args.command == "memory":
        if args.action == "save":
            _print(store.remember(args.key, args.value, args.category))
        elif args.action == "search":
            _print(store.search_memories(args.query))
        elif args.action == "correct":
            _print(to_dict(app.tools.run(ActionRequest("memory.correct", {"memory_id": args.id, "value": args.value,
                                                                         "reason": args.reason}, Origin.USER_COMMAND))))
        elif args.action == "history":
            _print(store.memory.history(args.id))
        elif args.action == "forget":
            proposal = app.tools.run(ActionRequest("memory.forget", {"memory_id": args.id}, Origin.USER_COMMAND))
            if not args.yes:
                _print({"status": proposal.status.value, "operation_id": proposal.operation_id,
                        "message": "Forgetting is irreversible. Re-run with --yes, or run "
                                   f"`jarvis approve {proposal.operation_id}`."})
            else:
                _print(to_dict(app.tools.approve(proposal.operation_id, approved_by="user:cli --yes")))
        elif args.action == "conflicts":
            _print(store.memory.open_conflicts(50))
        elif args.action == "resolve":
            _print(store.memory.resolve_conflict(args.conflict_id, keep_id=args.keep, reason=args.reason))
        else:
            data = _export(app)
            if args.out:
                Path(args.out).write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n",
                                          encoding="utf-8")
                make_private(args.out)    # an export is a full copy of personal data
                _print({"exported": args.out, "memories": len(data["memories"]), "tasks": len(data["tasks"])})
            else:
                _print(data)
    elif args.command == "ask":
        result = app.ask(args.message)
        if args.text:
            print(result["answer"])
        else:
            _print(result)
    elif args.command == "chat":
        _chat(app)
    elif args.command == "voice":
        from .voice import TermuxVoice, probe, voice_loop
        found = probe()
        if not found.available:
            raise SystemExit(f"Voice is unavailable: {found.reason}. Text chat still works: `jarvis chat`.")
        summary = voice_loop(app.ask, TermuxVoice(found), max_turns=args.max_turns)
        if summary["session_id"]:
            app.store.state.end_session(summary["session_id"], "user_exit")
        print(f"Voice session saved ({summary['turns']} turn(s)).")
    elif args.command == "resume":
        print(app.ask("where were we?")["answer"])
    elif args.command == "trace":
        cycle_id = args.cycle_id or store.last_cycle_id()
        cycle = store.cycle(cycle_id) if cycle_id else None
        if not cycle:
            raise SystemExit("No such cycle")
        events = [{k: e[k] for k in ("id", "ts", "kind", "actor", "target", "decision", "operation_id", "memory_id",
                                     "task_id", "goal_id")} for e in store.events.for_cycle(cycle_id)]
        _print({"cycle": cycle, "events": events})
    elif args.command == "events":
        _print(store.events.recent(args.limit, include_sensitive=args.include_sensitive))
    elif args.command == "ops":
        _print(store.operations.list(status=args.status, limit=100))
    elif args.command == "approve":
        _print(to_dict(app.tools.approve(args.id, approved_by="user:cli")))
    elif args.command == "deny":
        _print(to_dict(app.tools.deny(args.id)))
    elif args.command == "capabilities":
        _print(app.capabilities())
    elif args.command == "identity":
        _print({"identity": app.identity, "runtime_history": store.state.runtime_history()})
    elif args.command == "state":
        _print(app.state())
    elif args.command == "doctor":
        backups = sorted(p.name for p in (settings.home / "backups").glob("*.sqlite3")) if (settings.home / "backups").is_dir() else []
        _print({"integrity": store.integrity(), "migration_at_startup": {
            "from": store.migration.from_version, "to": store.migration.to_version,
            "applied": list(store.migration.applied), "backup": str(store.migration.backup) if store.migration.backup else None},
            "backups": backups, "configuration": settings.describe(), "recovery_at_startup": app.recovery,
            "python": sys.version.split()[0],
            "permissions": {"home": file_mode(settings.home), "database": file_mode(store.path),
                            "note": "home and database should be readable only by you (0o700 / 0o600)"}})
    elif args.command == "serve":
        import socket
        from http.server import ThreadingHTTPServer

        from .api import load_or_create_token
        from .redact import register_secret
        host, port = args.host or settings.host, args.port or settings.port
        token, token_path = load_or_create_token(settings.home)
        register_secret(token)
        server_class = ThreadingHTTPServer
        if ":" in host:                       # IPv6 loopback literal (::1) needs an IPv6 socket
            server_class = type("ThreadingHTTPServerV6", (ThreadingHTTPServer,), {"address_family": socket.AF_INET6})
        server = server_class((host, port), make_handler(app, token=token))
        shown = f"[{host}]" if ":" in host else host
        print(f"JARVIS v{__version__} listening on http://{shown}:{port}", flush=True)
        print(f"Requests (except GET /health) need 'Authorization: Bearer <token>'. The token is stored in "
              f"{token_path}, readable only by your user account; delete the file to rotate it.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
