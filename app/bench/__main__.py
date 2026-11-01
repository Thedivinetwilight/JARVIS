"""``python3 -m app.bench`` — run one benchmark, locally, with nothing downloaded.

Exit codes (documented in ``app/bench/README.md`` and asserted by the tests):

``0``  the run produced a valid result document;
``2``  the command line or a supplied file was unusable (bad arguments, bad criteria, bad fixture
       pack) — nothing was measured;
``3``  the result document failed its own validation and was not written;
``4``  the run failed to measure anything (no sample survived);
``5``  an adapter attempted network access; the run was abandoned and the result is ``violated``;
``6``  no adapter was supplied, or the supplied adapter or model path does not exist.

A missing adapter is its own exit code because it is the single most likely first user error: this
benchmark has no default model and never downloads one.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import (BENCH_VERSION, EXIT_INVALID_RESULT, EXIT_MISSING_ADAPTER, EXIT_NETWORK_VIOLATION,
               EXIT_OK, EXIT_RUN_FAILED, EXIT_USAGE, GATES)
from . import languages as languages_module
from .adapter import AdapterError
from .decision import CriteriaError
from .fixtures import FixtureError
from .results import ResultError
from .runner import RunConfig, build_plan, run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m app.bench",
        description=("Measure an operator-supplied local speech adapter on a fixed workload. "
                     "No default model, no download, no network, no model selection."),
        epilog=("Gates are not moved by this tool: G-O1 stays blocked, Q19 (languages) and Q20 "
                "(server) stay open, and a completed run is not a capability."),
    )
    parser.add_argument("--asr-adapter", metavar="PATH",
                        help="local file implementing the speech-recognition adapter contract")
    parser.add_argument("--tts-adapter", metavar="PATH",
                        help="local file implementing the speech-generation adapter contract")
    parser.add_argument("--turn-adapter", metavar="PATH",
                        help="optional local file implementing the turn adapter contract")
    parser.add_argument("--asr-model", metavar="PATH", help="local model file or directory for ASR")
    parser.add_argument("--tts-model", metavar="PATH", help="local model file or directory for TTS")
    parser.add_argument("--asr-model-sha256", metavar="HEX",
                        help="expected hash of the ASR model; a mismatch is fatal")
    parser.add_argument("--tts-model-sha256", metavar="HEX",
                        help="expected hash of the TTS model; a mismatch is fatal")
    parser.add_argument("--fixtures", metavar="DIR",
                        help="licensed fixture pack directory (enables WER, per-language results)")
    parser.add_argument("--languages", metavar="CODES",
                        help="comma-separated codes from the PR-1 proposal; nothing is scheduled "
                             "by default")
    parser.add_argument("--repeat", type=int, default=3, help="measured rounds per item (1-50)")
    parser.add_argument("--warmup", type=int, default=1,
                        help="warm-up rounds per item, discarded before measurement (0-20)")
    parser.add_argument("--seed", type=int, default=20261101,
                        help="seed for synthetic fixtures and bootstrap intervals")
    parser.add_argument("--host-role", choices=("development", "dedicated_server"),
                        default="development",
                        help="where this run happens; only the owner's dedicated server is "
                             "decision-relevant (Q20 open)")
    parser.add_argument("--timeout", type=float, default=60.0, metavar="SECONDS",
                        help="per-item deadline for an adapter request")
    parser.add_argument("--allow-loopback-ipc", action="store_true",
                        help="permit loopback connections for adapters that need a local daemon; "
                             "recorded as a deviation from the default deny-all sandbox")
    parser.add_argument("--no-hash-models", action="store_true",
                        help="skip hashing a large model directory (recorded as hash_mode "
                             "not_computed)")
    parser.add_argument("--include-hostname", action="store_true",
                        help="record the host name; off by default")
    parser.add_argument("--host-facts", metavar="PATH",
                        help="operator-declared host facts JSON for a host that cannot be probed")
    parser.add_argument("--criteria", metavar="PATH",
                        help="pre-registered criteria JSON; without it the decision is "
                             "not_decidable")
    parser.add_argument("--out", metavar="DIR", default="bench-out",
                        help="output directory (default: bench-out)")
    parser.add_argument("--plan-only", action="store_true",
                        help="write the plan and exit without running anything")
    parser.add_argument("--version", action="version",
                        version=f"app.bench {BENCH_VERSION}")
    return parser


def _parse_languages(text: str | None) -> list[str]:
    if not text:
        return []
    return [code.strip() for code in text.split(",") if code.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    out_dir = Path(arguments.out)

    config = RunConfig(
        out_dir=out_dir,
        fixtures_dir=Path(arguments.fixtures) if arguments.fixtures else None,
        asr_adapter=arguments.asr_adapter,
        tts_adapter=arguments.tts_adapter,
        turn_adapter=arguments.turn_adapter,
        asr_model=arguments.asr_model,
        tts_model=arguments.tts_model,
        asr_model_sha256=arguments.asr_model_sha256,
        tts_model_sha256=arguments.tts_model_sha256,
        languages=_parse_languages(arguments.languages),
        repeat=arguments.repeat,
        warmup=arguments.warmup,
        seed=arguments.seed,
        host_role=arguments.host_role,
        timeout_s=arguments.timeout,
        allow_loopback=arguments.allow_loopback_ipc,
        hash_models=not arguments.no_hash_models,
        include_hostname=arguments.include_hostname,
        host_facts=Path(arguments.host_facts) if arguments.host_facts else None,
        criteria=Path(arguments.criteria) if arguments.criteria else None,
    )

    problems = config.validate()
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return EXIT_USAGE

    if not arguments.plan_only and not (arguments.asr_adapter or arguments.tts_adapter):
        print("error: no adapter supplied.\n"
              "       This benchmark has no default model and never downloads one. Name a local\n"
              "       file with --asr-adapter and/or --tts-adapter, or use --plan-only to write\n"
              "       the plan without running it.", file=sys.stderr)
        return EXIT_MISSING_ADAPTER

    print(f"app.bench {BENCH_VERSION} · gates: "
          + ", ".join(f"{name}={value}" for name, value in GATES.items()))
    print(f"output: {out_dir}")

    if arguments.plan_only:
        try:
            plan = build_plan(config, require_adapters=False)
        except (AdapterError, FixtureError, languages_module.LanguageMatrixError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_USAGE
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "plan.json").write_text(
            json.dumps(plan.document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"plan: {out_dir / 'plan.json'} · {plan.document['task_count']} tasks · "
              f"hash {plan.document['plan_hash'][:16]}…")
        print("nothing was run (--plan-only); no result.json, no report.md")
        return EXIT_OK

    try:
        result, exit_code = run(config)
    except AdapterError as exc:
        message = str(exc)
        print(f"error: {message}", file=sys.stderr)
        if "no adapter supplied" in message or "not found" in message or "does not exist" in message:
            return EXIT_MISSING_ADAPTER
        return EXIT_USAGE
    except CriteriaError as exc:
        print(f"error: criteria rejected: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except FixtureError as exc:
        print(f"error: fixture pack rejected: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except ResultError as exc:
        for problem in exc.problems:
            print(f"error: invalid result: {problem}", file=sys.stderr)
        print("the result document was NOT written", file=sys.stderr)
        return EXIT_INVALID_RESULT

    run_document = result.get("run") or {}
    decision = (result.get("decision") or {}).get("decision")
    print(f"status: {run_document.get('status')} · samples: {run_document.get('samples')} · "
          f"failures: {run_document.get('failures')}")
    print(f"decision: {decision}")
    for reason in (result.get("decision") or {}).get("reasons", [])[:6]:
        print(f"  - {reason}")
    print(f"result: {out_dir / 'result.json'}")
    print(f"report: {out_dir / 'report.md'}")
    print("voice processing is still unavailable in the application; this run is a measurement")

    if exit_code not in (EXIT_OK, EXIT_RUN_FAILED, EXIT_NETWORK_VIOLATION):
        return exit_code
    criteria = (result.get("decision") or {}).get("criteria") or {}
    if criteria.get("error"):
        print(f"error: criteria rejected: {criteria['error']}", file=sys.stderr)
        return EXIT_USAGE
    return exit_code


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())


__all__ = ["build_parser", "main"]
