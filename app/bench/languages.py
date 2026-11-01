"""The proposed language matrix, used strictly as an unapproved evaluation matrix.

The fifteen languages come from ``docs/product/capabilities.json`` proposal **PR-1**, which is
*not a requirement*: Q19 is open, the owner has not set the list, the order or the fluency bar, and
no language is claimed to work. This module reads that proposal (it never writes it) and gives every
language three honest states:

* ``not_run``      — not scheduled for this run, or scheduled with nothing to run it on;
* ``not_measured`` — scheduled and attempted, but no metric survived the sample gates;
* ``measured``     — at least one metric was actually computed from this language's own fixtures.

A language is only ever ``measured`` from its **own** fixtures. There is no code path that copies a
result between languages, because "close enough" is how a fluency claim gets invented.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CAPABILITIES_JSON = REPO_ROOT / "docs" / "product" / "capabilities.json"

PROPOSAL_ID = "PR-1"

STATUS_NOT_RUN = "not_run"
STATUS_NOT_MEASURED = "not_measured"
STATUS_MEASURED = "measured"
STATUSES = (STATUS_NOT_RUN, STATUS_NOT_MEASURED, STATUS_MEASURED)

#: The only tier value this benchmark may emit. Support tiers are C-03-AC4 territory and need
#: measured results plus a bar the owner still has to define (Q19).
TIER_NOT_ASSIGNED = "not_assigned"

#: A label this benchmark can never produce. Kept as a constant so tests can assert its absence.
FORBIDDEN_FLUENCY_LABELS = ("fluent", "supported", "production-ready", "verified")


class LanguageMatrixError(Exception):
    """The proposal could not be read, or a requested language is not in it."""


@dataclass(frozen=True)
class Language:
    code: str
    name: str
    proposal_status: str

    def to_dict(self) -> dict:
        return {"code": self.code, "name": self.name, "proposal_status": self.proposal_status}


@dataclass
class LanguageEntry:
    """One language's place in a run, including everything that was not measured."""

    code: str
    name: str
    scheduled: bool = False
    status: str = STATUS_NOT_RUN
    fixtures: int = 0
    samples: dict = field(default_factory=dict)
    latency: dict | None = None
    wer: dict | None = None
    entity_accuracy: dict | None = None
    task_success: dict | None = None
    #: Values that were computed but did **not** clear their pre-registered sample gate. They are
    #: kept here, never in the metric fields, so a reader can see the arithmetic without mistaking
    #: it for a reportable number.
    below_gate: dict = field(default_factory=dict)
    not_measured: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "proposal": PROPOSAL_ID,
            "proposal_is_a_requirement": False,
            "scheduled": self.scheduled,
            "status": self.status,
            "fixtures": self.fixtures,
            "samples": self.samples,
            "latency": self.latency,
            "wer": self.wer,
            "entity_accuracy": self.entity_accuracy,
            "task_success": self.task_success,
            "below_gate": dict(self.below_gate),
            # Never anything else: see the module docstring.
            "tier": TIER_NOT_ASSIGNED,
            "fluency_claim": None,
            "not_measured": self.not_measured,
            "notes": list(self.notes),
        }


def load_proposal(path: Path | None = None) -> dict:
    """Read PR-1 from the capability index without modifying it."""
    path = path or CAPABILITIES_JSON
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:  # pragma: no cover - environment failure
        raise LanguageMatrixError(f"cannot read the language proposal: {exc}") from exc
    proposals = {item["id"]: item for item in document.get("proposals", [])}
    if PROPOSAL_ID not in proposals:
        raise LanguageMatrixError(f"{PROPOSAL_ID} is missing from {path.name}")
    proposal = proposals[PROPOSAL_ID]
    if not proposal.get("languages"):
        raise LanguageMatrixError(f"{PROPOSAL_ID} has no languages")
    return proposal


def load_matrix(path: Path | None = None) -> list[Language]:
    """The fifteen proposed languages, in proposal order, all unmeasured by definition."""
    proposal = load_proposal(path)
    return [
        Language(code=item["code"], name=item["name"], proposal_status=item["status"])
        for item in proposal["languages"]
    ]


def codes(path: Path | None = None) -> list[str]:
    return [language.code for language in load_matrix(path)]


def select(requested: list[str] | None, path: Path | None = None) -> list[Language]:
    """Resolve a ``--languages`` selection against the matrix.

    An empty or missing selection schedules **nothing**: a benchmark that silently picks a language
    would be making a decision the owner has not made. Unknown codes are refused.
    """
    matrix = load_matrix(path)
    if not requested:
        return []
    known = {language.code: language for language in matrix}
    unknown = [code for code in requested if code not in known]
    if unknown:
        raise LanguageMatrixError(
            "not in the proposed matrix (Q19 is open, so the list cannot be extended here): "
            + ", ".join(sorted(unknown))
        )
    seen: list[str] = []
    for code in requested:
        if code not in seen:
            seen.append(code)
    return [known[code] for code in seen]


def build_entries(
    matrix: list[Language],
    *,
    scheduled: list[str],
    fixture_counts: dict[str, int],
) -> list[LanguageEntry]:
    """One entry per proposed language: scheduled or not, measured or explicitly not."""
    entries: list[LanguageEntry] = []
    for language in matrix:
        entry = LanguageEntry(code=language.code, name=language.name,
                              scheduled=language.code in scheduled)
        entry.fixtures = fixture_counts.get(language.code, 0)
        if not entry.scheduled:
            entry.not_measured = {
                "everything": "not scheduled for this run; the matrix is not a decision and an "
                              "unrun language has no result",
            }
            entry.notes.append("not scheduled: no language inherits another language's result")
        elif entry.fixtures == 0:
            entry.not_measured = {
                "everything": "no licensed fixture in this language was supplied; synthetic "
                              "acoustic fixtures are language-neutral and cannot stand in",
            }
        entries.append(entry)
    return entries


def validate_entries(entries: list[dict]) -> list[str]:
    """Structural checks that keep a language from acquiring evidence it does not have."""
    problems: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        code = entry.get("code")
        if not code:
            problems.append("a language entry has no code")
            continue
        if code in seen:
            problems.append(f"{code}: duplicate language entry")
        seen.add(code)
        status = entry.get("status")
        if status not in STATUSES:
            problems.append(f"{code}: unknown status {status!r}")
        if entry.get("tier") != TIER_NOT_ASSIGNED:
            problems.append(f"{code}: tier must be {TIER_NOT_ASSIGNED!r}, got {entry.get('tier')!r}")
        if entry.get("fluency_claim") is not None:
            problems.append(f"{code}: a fluency claim is never allowed, got {entry['fluency_claim']!r}")
        if status != STATUS_MEASURED:
            if not entry.get("not_measured"):
                problems.append(f"{code}: status {status} requires an explicit not_measured reason")
            for metric in ("wer", "latency", "entity_accuracy", "task_success"):
                if entry.get(metric):
                    problems.append(f"{code}: status {status} cannot carry a {metric} result")
            for metric, block in (entry.get("below_gate") or {}).items():
                if not isinstance(block, dict) or block.get("gate_cleared") is not False:
                    problems.append(
                        f"{code}: below_gate[{metric!r}] must be a block that states gate_cleared: false")
        else:
            measured = [metric for metric in ("wer", "latency", "entity_accuracy", "task_success")
                        if entry.get(metric) and not entry[metric].get("not_measured")]
            if not measured:
                problems.append(f"{code}: status measured requires at least one reported metric")
    return problems
