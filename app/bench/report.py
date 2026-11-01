"""The human-readable report. Every table carries its sample count; nothing is rounded into a claim.

The report is generated from ``result.json`` and nothing else, so it cannot say more than the
machine-readable document does. Numbers that failed their gate are shown in a separate
*below gate* column so they are visible but unmistakably not results.
"""

from __future__ import annotations

from pathlib import Path

from . import BENCH_VERSION
from . import languages as languages_module

HEADLINE = (
    "> **This is a measurement of a local workload, not a capability.** Nothing in this report "
    "enables voice processing in the J.A.R.V.I.S. application, selects a model, a vendor or a "
    "server, or moves gate G-O1. Voice input and output remain unavailable in the product."
)


def _milliseconds(value) -> str:
    """Three decimals below 10 ms: a single adapter call can legitimately be microseconds."""
    if value is None:
        return "—"
    return f"{value:.1f} ms" if abs(value) >= 10 else f"{value:.3f} ms"


def _fraction(value) -> str:
    return "—" if value is None else f"{value:.4f}"


def _latency_cell(block: dict | None) -> str:
    """One cell for 'the latency of this thing', used where a single column has to carry it."""
    if not block or block.get("n") in (None, 0):
        return "not measured"
    if block.get("p50_ms") is None:
        return f"below gate (n={block['n']}, mean {_milliseconds(block.get('mean_ms'))})"
    return (f"p50 {_milliseconds(block['p50_ms'])} / p95 {_milliseconds(block.get('p95_ms'))} "
            f"(n={block['n']})")


def _quantile_cell(block: dict | None, key: str) -> str:
    """One quantile and its sample count, or the reason it is not reported."""
    if not block or block.get("n") in (None, 0):
        return "not measured"
    value = block.get(key)
    if value is None:
        return f"below gate (n={block['n']})"
    return f"{_milliseconds(value)} (n={block['n']})"


def _not_measured_rows(document: dict) -> list[str]:
    rows = ["| Metric | Status |", "| --- | --- |"]
    for key, reason in sorted((document.get("not_measured") or {}).items()):
        rows.append(f"| `{key}` | {'measured' if reason is None else reason} |")
    return rows


def _phase_rows(document: dict) -> list[str]:
    overall = document.get("overall") or {}
    rows = ["| Phase | Samples | p50 | p95 | First output |",
            "| --- | --- | --- | --- | --- |"]
    for phase, block in sorted((overall.get("by_phase") or {}).items()):
        latency = block.get("latency_all") or {}
        rows.append(
            f"| {phase} | {block.get('samples', 0)} | {_quantile_cell(latency, 'p50_ms')} | "
            f"{_quantile_cell(latency, 'p95_ms')} | "
            f"{_quantile_cell(block.get('first_output'), 'p50_ms')} |"
        )
    if len(rows) == 2:
        rows.append("| — | 0 | not measured | not measured | not measured |")
    return rows


def _not_available(block: dict | None, key: str) -> str:
    if not block:
        return "—"
    return _milliseconds(block.get(key))


def _language_rows(document: dict) -> list[str]:
    rows = ["| Language | Scheduled | Status | Fixtures | ASR n | Latency | WER (mean, n) | "
            "Entity accuracy (n) | Task success (n) | Tier |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for entry in document.get("languages", []):
        wer = entry.get("wer") or {}
        wer_cell = ("—" if entry.get("status") != languages_module.STATUS_MEASURED
                    else f"{_fraction(wer.get('mean'))} (n={wer.get('n', 0)})"
                    if wer.get("mean") is not None else "below gate")
        entity = entry.get("entity_accuracy") or {}
        entity_cell = ("—" if entity.get("mean") is None
                       else f"{_fraction(entity.get('mean'))} (n={entity.get('n', 0)})")
        success = entry.get("task_success") or {}
        success_cell = ("—" if success.get("value") is None
                        else f"{_fraction(success.get('value'))} (n={success.get('n', 0)})")
        rows.append(
            f"| {entry.get('code')} | {'yes' if entry.get('scheduled') else 'no'} | "
            f"{entry.get('status')} | {entry.get('fixtures', 0)} | "
            f"{(entry.get('samples') or {}).get('asr', 0)} | "
            f"{_latency_cell(entry.get('latency')) if entry.get('status') == 'measured' else 'not measured'} | "
            f"{wer_cell} | {entity_cell} | {success_cell} | {entry.get('tier')} |"
        )
    return rows


def _condition_rows(document: dict) -> list[str]:
    rows = ["| Condition | Phase | Samples | p50 | p95 |", "| --- | --- | --- | --- | --- |"]
    seen = False
    for block in document.get("conditions") or []:
        for phase, latency in sorted((block.get("by_phase") or {}).items()):
            seen = True
            rows.append(f"| {block['condition']} | {phase} | {latency.get('n', 0)} | "
                        f"{_not_available(latency, 'p50_ms')} | {_not_available(latency, 'p95_ms')} |")
    if not seen:
        rows.append("| — | — | 0 | not measured | not measured |")
    return rows


def _host_rows(document: dict) -> list[str]:
    host = document.get("host") or {}
    cpu = host.get("cpu") or {}
    memory = host.get("memory") or {}
    gpu = host.get("gpu") or {}
    thermal = host.get("thermal") or {}
    power = (host.get("power") or {}).get("measurement") or {}
    rows = ["| Item | Value |", "| --- | --- |"]
    rows.append(f"| Host id | `{host.get('host_id')}` |")
    rows.append(f"| Role | {document.get('host_role')} "
                f"({document.get('host_role_source') or 'operator declaration'}) |")
    rows.append(f"| OS | {(host.get('os') or {}).get('pretty_name') or (host.get('os') or {}).get('status')} |")
    rows.append(f"| Kernel | {(host.get('kernel') or {}).get('release')} |")
    rows.append(f"| Python | {(host.get('runtime') or {}).get('python_version')} |")
    rows.append(f"| CPU | {cpu.get('model')} ({cpu.get('physical_cores')} physical / "
                f"{cpu.get('logical_cores')} logical) |")
    rows.append(f"| Memory | {memory.get('total_mb')} MB |" if memory.get("total_mb")
                else f"| Memory | not available ({memory.get('reason')}) |")
    rows.append(f"| GPU | {gpu.get('name') or 'not available'}"
                f"{' (' + str(gpu.get('memory_mb')) + ' MB)' if gpu.get('memory_mb') else ''} |"
                if gpu.get("status") == "available" else f"| GPU | not available ({gpu.get('reason')}) |")
    rows.append(f"| Thermal | {thermal.get('status')}"
                f"{' — ' + str(thermal.get('reason')) if thermal.get('status') != 'available' else ''} |")
    rows.append(f"| Power | {power.get('status')}"
                f"{' — ' + str(power.get('reason')) if power.get('status') != 'available' else ''} |")
    declared = host.get("declared_by_operator") or {}
    if declared:
        rows.append("| Operator-declared facts | present, `source: operator_declared` (not probed) |")
    return rows


def _adapter_rows(document: dict) -> list[str]:
    adapters = (document.get("adapters") or {}).get("declared") or {}
    models = (document.get("adapters") or {}).get("models") or {}
    runtime = (document.get("adapters") or {}).get("runtime") or {}
    rows = ["| Role | Adapter | Adapter hash | Model | Model hash mode | Model hash | Load |",
            "| --- | --- | --- | --- | --- | --- | --- |"]
    for role in ("asr", "tts", "turn"):
        adapter = adapters.get(role) or {}
        model = models.get(role) or {}
        loaded = runtime.get(role) or {}
        if not adapter and not model:
            rows.append(f"| {role} | not supplied | — | — | — | — | — |")
            continue
        rows.append(
            f"| {role} | {adapter.get('display_path') or 'not supplied'} | "
            f"{(adapter.get('sha256') or '—')[:16]} | {model.get('display_path') or '—'} | "
            f"{model.get('hash_mode') or '—'} | {(model.get('sha256') or '—')[:16]} | "
            f"{_milliseconds(loaded.get('load_ms')) if loaded.get('load_ms') else '—'} |"
        )
    return rows


def _failure_rows(document: dict) -> list[str]:
    failures = document.get("failures") or []
    if not failures:
        return ["No item failed."]
    rows = ["| Task | Phase | Code | Message |", "| --- | --- | --- | --- |"]
    for failure in failures[:40]:
        rows.append(f"| `{failure.get('task_id')}` | {failure.get('phase')} | "
                    f"{failure.get('code')} | {str(failure.get('message'))[:120]} |")
    if len(failures) > 40:
        rows.append(f"| … | | | {len(failures) - 40} more in result.json |")
    return rows


def render_markdown(document: dict) -> str:
    run = document.get("run") or {}
    identity = document.get("identity") or {}
    network = document.get("network") or {}
    decision = document.get("decision") or {}
    overall = document.get("overall") or {}
    throughput = overall.get("throughput") or {}
    lines: list[str] = []

    lines.append("# Local voice workload benchmark — run report")
    lines.append("")
    lines.append(HEADLINE)
    lines.append("")
    lines.append(f"- Benchmark version: `{BENCH_VERSION}`")
    lines.append(f"- Run id: `{run.get('id')}`")
    lines.append(f"- Schema: `{document.get('schema')}`")
    lines.append(f"- Result: **{run.get('status')}** (exit code {run.get('exit_code')})")
    lines.append(f"- Decision: **{decision.get('decision')}**")
    lines.append(f"- Host role: **{document.get('host_role')}**")
    if document.get("host_role") != "dedicated_server":
        lines.append("")
        lines.append("> This run was made on a development host. Its numbers are not evidence about "
                     "the owner's dedicated server, whose specification is still open (Q20).")
    lines.append("")
    lines.append("## What this run establishes")
    lines.append("")
    measured = document.get("not_measured") or {}
    measured_keys = sorted(key for key, reason in measured.items() if reason is None)
    lines.append("Measured: " + (", ".join(f"`{key}`" for key in measured_keys)
                                 if measured_keys else "**nothing**") + ".")
    lines.append("")
    lines.append("Not measured: " + ", ".join(
        f"`{key}`" for key, reason in sorted(measured.items()) if reason is not None) + ".")
    lines.append("")
    lines.append("## Reproducing this run")
    lines.append("")
    command = identity.get("reproduce_command")
    lines.append("```")
    lines.append(command or "python3 -m app.bench --help")
    lines.append("```")
    lines.append("")
    lines.append(f"Plan hash: `{identity.get('plan_hash')}`  ")
    git = identity.get("git") or {}
    lines.append(f"Git: `{(git.get('commit') or 'unknown')[:12]}`"
                 f"{' (working tree dirty)' if git.get('dirty') else ''}")
    lines.append("")
    lines.append("## Method, fixed before the run")
    lines.append("")
    methodology = document.get("methodology") or {}
    lines.append(f"- Repeat: {((document.get('plan') or {}).get('repeat'))} · warm-up: "
                 f"{((document.get('plan') or {}).get('warmup'))} · seed: "
                 f"{((document.get('plan') or {}).get('seed'))}")
    lines.append(f"- Minimum samples: latency {methodology.get('min_samples_latency')}, "
                 f"rate {methodology.get('min_samples_rate')}, WER {methodology.get('min_samples_wer')}")
    lines.append(f"- Percentiles: {methodology.get('percentile_method')}")
    for rule in methodology.get("rules", []):
        lines.append(f"- {rule}")
    boundaries = (document.get("plan") or {}).get("timing_boundaries") or {}
    for name, text in boundaries.items():
        lines.append(f"- **{name}**: {text}")
    lines.append("")
    lines.append("## Host")
    lines.append("")
    lines.extend(_host_rows(document))
    lines.append("")
    lines.append("## Adapters and models (operator-supplied)")
    lines.append("")
    lines.extend(_adapter_rows(document))
    lines.append("")
    lines.append("## Latency by phase")
    lines.append("")
    lines.extend(_phase_rows(document))
    lines.append("")
    lines.append("## Throughput")
    lines.append("")
    lines.append(f"- ASR audio seconds processed: {throughput.get('asr_audio_seconds')}")
    lines.append(f"- ASR wall seconds: {throughput.get('asr_wall_seconds')}")
    lines.append(f"- ASR audio seconds per wall second: "
                 f"{throughput.get('asr_audio_seconds_per_wall_second')}")
    lines.append(f"- Real-time factor medians: ASR {throughput.get('asr_real_time_factor_median')}, "
                 f"TTS {throughput.get('tts_real_time_factor_median')} "
                 f"(n={throughput.get('n', 0)})")
    lines.append(f"- Peak adapter worker memory: {overall.get('peak_worker_memory_mb')} MB")
    lines.append("")
    lines.append("## Cold and warm")
    lines.append("")
    cold_warm = overall.get("cold_warm") or {}
    if not cold_warm:
        lines.append("No phase produced a measured item.")
    else:
        lines.append("| Phase | Cold (one observation) | Warm p50 | Warm n |")
        lines.append("| --- | --- | --- | --- |")
        for phase, block in sorted(cold_warm.items()):
            warm = block.get("warm") or {}
            lines.append(f"| {phase} | {_milliseconds(block.get('cold_latency_ms'))} | "
                         f"{_not_available(warm, 'p50_ms')} | {warm.get('n', 0)} |")
        lines.append("")
        lines.append("Warm-up items were discarded before measurement; a single cold observation "
                     "is descriptive and is never given a percentile.")
    lines.append("")
    lines.append("## Per language")
    lines.append("")
    lines.append("PR-1 is a proposal (Q19 open). A language below is reported only from its own "
                 "licensed fixtures; nothing is carried across languages and no tier is assigned.")
    lines.append("")
    lines.extend(_language_rows(document))
    below = [entry for entry in document.get("languages", []) if entry.get("below_gate")]
    if below:
        lines.append("")
        lines.append("### Values below their gate (not results)")
        lines.append("")
        for entry in below:
            for metric, block in entry["below_gate"].items():
                lines.append(f"- `{entry['code']}` {metric}: n={block.get('n')}, "
                             f"gate_cleared=false — {entry.get('not_measured', {}).get('everything')}")
    lines.append("")
    lines.append("## Audio conditions")
    lines.append("")
    lines.extend(_condition_rows(document))
    lines.append("")
    lines.append("## Local-only enforcement")
    lines.append("")
    lines.append(f"- Socket connections and name resolution denied before the adapter was imported: "
                 f"**{'yes' if network.get('denied') else 'see result.json'}**")
    lines.append(f"- Loopback IPC permitted (recorded deviation): **{network.get('loopback_allowed')}**")
    lines.append(f"- Attempts recorded: {network.get('attempts', 0)} · denials: "
                 f"{network.get('denials', 0)}")
    lines.append(f"- Worker log digest: `{network.get('log_sha256') or 'not computed'}`")
    lines.append("")
    lines.append("## Failures")
    lines.append("")
    lines.extend(_failure_rows(document))
    lines.append("")
    lines.append("## Decision")
    lines.append("")
    lines.append(f"**{decision.get('decision')}**")
    lines.append("")
    for reason in decision.get("reasons", []):
        lines.append(f"- {reason}")
    blocks = decision.get("per_language") or []
    if blocks:
        lines.append("")
        lines.append("| Language | Verdict | Reason |")
        lines.append("| --- | --- | --- |")
        for block in blocks:
            lines.append(f"| {block.get('language')} | {block.get('verdict')} | "
                         f"{'; '.join(block.get('reasons', []))[:200]} |")
    criteria = decision.get("criteria") or {}
    lines.append("")
    lines.append(f"Criteria: {'`' + str(criteria.get('criteria_id')) + '`' if criteria.get('supplied') else 'none supplied'}"
                 f"{' (sha256 ' + str(criteria.get('sha256'))[:16] + '…)' if criteria.get('sha256') else ''}")
    lines.append("")
    lines.append("Still required before any voice implementation:")
    lines.append("")
    for item in decision.get("outstanding_before_voice_implementation", []):
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## What was not measured")
    lines.append("")
    lines.extend(_not_measured_rows(document))
    lines.append("")
    lines.append("## Claims this run does not make")
    lines.append("")
    claims = document.get("claims") or {}
    for key, value in sorted(claims.items()):
        lines.append(f"- `{key}`: {value}")
    gates = document.get("gate_status") or {}
    lines.append("")
    lines.append("Gate status (unchanged by this run): "
                 + ", ".join(f"`{name}`={value}" for name, value in sorted(gates.items())))
    lines.append("")
    return "\n".join(lines)


def write_report(path: Path, document: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(document), encoding="utf-8")


__all__ = ["HEADLINE", "render_markdown", "write_report"]
