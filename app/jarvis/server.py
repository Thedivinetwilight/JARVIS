"""Process entry point: validate configuration, migrate, install the egress guard, serve."""

from __future__ import annotations

import logging
import signal
import sys
import threading

from .app import App
from .config import Config, load_config
from .db import Store
from .errors import ConfigError, JarvisError
from .http import bound_address, build_server

LOGGER = logging.getLogger("jarvis.server")


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )


def prepare_app(config: Config, *, migrate: bool = False) -> App:
    """Build the application, migrating only when explicitly asked."""
    store = Store(config.storage.database_path)
    if migrate:
        result = store.migrate()
        LOGGER.info(
            "database ready at %s (%d migration(s) applied, %d already present)",
            config.storage.database_path,
            len(result.applied),
            result.already_applied,
        )
    status = store.status()
    if not status["up_to_date"]:
        LOGGER.warning(
            "pending migrations: %s (readiness will report not-ready)", ", ".join(status["pending"])
        )
    return App(config, store=store)


def serve(config: Config, *, migrate: bool = True) -> int:
    configure_logging(config.logging_level)
    app = prepare_app(config, migrate=migrate)
    app.audio.purge_expired()

    server = build_server(config, app)
    host, port = bound_address(server)
    LOGGER.info("jarvis %s listening on %s:%s (tls=%s)", app.version, host, port,
                config.server.tls_enabled)
    LOGGER.info("egress policy: default deny, declarations: %d (all disabled)",
                len(app.guard.policy()["declared_external_services"]))

    stopping = threading.Event()

    def _stop(signum, frame) -> None:  # noqa: ANN001 - signal handler
        LOGGER.info("signal %s received, shutting down", signum)
        stopping.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _stop)
        except ValueError:  # pragma: no cover - not the main thread
            pass

    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    from .cli import main as cli_main

    return cli_main(argv)


def run_foreground(config_path: str, *, migrate: bool = True) -> int:  # pragma: no cover - glue
    try:
        config = load_config(config_path)
    except (ConfigError, JarvisError) as exc:
        print(f"configuration refused: {exc}", file=sys.stderr)
        return 2
    return serve(config, migrate=migrate)
