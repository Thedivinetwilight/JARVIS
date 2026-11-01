"""Local voice-workload benchmark (measurement tooling, not a capability).

This package measures an operator-supplied, local candidate adapter on a fixed, reproducible
workload. It does **not** select a model, a provider, a speech stack or hardware, and running it
changes nothing about what the J.A.R.V.I.S. server can do: voice processing stays unavailable in
``app/jarvis`` (see ``/v1/limitations``).

Entry point::

    python3 -m app.bench --help

The four rules this package is built around:

1. **No default model and no download.** An adapter and a model path must be named explicitly on
   the command line; nothing is fetched, discovered or guessed.
2. **Local-only, fail closed.** The adapter runs in a separate process with every socket
   connection (and name lookup) denied before the adapter is imported. One attempt ends the run.
3. **Measurements, not decisions.** Results carry ``not_measured`` for everything that was not
   actually measured, and a decision is only ever ``rejected``, ``not_decidable`` or
   ``eligible_for_owner_review``. Gate G-O1 stays blocked, Q19 and Q20 stay open.
4. **No personal data.** Fixtures are synthetic or carry an explicit licence; results contain no
   audio, no user content, no secrets and no host name unless the operator asks for it.
"""

from __future__ import annotations

#: Version of the benchmark itself. Independent of the product specification version.
BENCH_VERSION = "0.1.0"

#: Identifier of the result document shape. Bump only on an incompatible change.
RESULT_SCHEMA = "jarvis.voice-bench/1"

#: Identifier of the fixture-pack document shape.
FIXTURE_SCHEMA = "jarvis.voice-fixtures/1"

#: Gate identifiers this benchmark reports on, with their status. It never moves them.
GATES: dict[str, str] = {
    "G-O1": "blocked",
    "G-W": "open",
    "G-C": "open",
    "G-LOCAL": "open",
}

#: Exit codes, documented in ``app/bench/README.md``.
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_INVALID_RESULT = 3
EXIT_RUN_FAILED = 4
EXIT_NETWORK_VIOLATION = 5
EXIT_MISSING_ADAPTER = 6

__all__ = [
    "BENCH_VERSION",
    "EXIT_INVALID_RESULT",
    "EXIT_MISSING_ADAPTER",
    "EXIT_NETWORK_VIOLATION",
    "EXIT_OK",
    "EXIT_RUN_FAILED",
    "EXIT_USAGE",
    "FIXTURE_SCHEMA",
    "GATES",
    "RESULT_SCHEMA",
]
