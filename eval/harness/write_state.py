"""Record-write state model for the synthetic memory store.

A restore writes each surviving record in ordered *parts*: the primary record
first, then one derived-surface entry per surface that holds a copy of it. A
write that is interrupted mid-way leaves the record ``partially_applied``: some
parts are in the store and the rest are not. That state is distinguishable from
an attempt that never reached the record (``not_started``), from a finished
write (``committed``) and from a partial write that was undone (``rolled_back``).

The states and the transitions between them are defined here once, so the
fixture loader, the executor, the reference solver and the scorer cannot drift
apart. Two consequences matter for scoring:

* a committed write is terminal -- re-applying it is what "duplicate effect"
  means, so ``committed -> committed`` is not a legal transition;
* ``rolled_back`` may be followed by ``not_started`` (a retry starts over) or by
  ``committed`` (a retry immediately rewrites the record), but a rolled-back
  write that is claimed while its parts are still in the store is not a rollback
  at all.

Nothing here touches a real store: the parts are dictionaries in memory and the
store belongs to the synthetic fixture.
"""
from __future__ import annotations

from typing import Any, Iterable

NOT_STARTED = "not_started"
PARTIALLY_APPLIED = "partially_applied"
COMMITTED = "committed"
ROLLED_BACK = "rolled_back"

#: Every state a record write may be reported in.
STATES = (NOT_STARTED, PARTIALLY_APPLIED, COMMITTED, ROLLED_BACK)

#: Terminal states: nothing may follow them for the same write.
TERMINAL = (COMMITTED,)

#: Legal transitions. ``committed`` is terminal, so re-committing a write is an
#: invalid sequence rather than a duplicate effect to be argued about.
TRANSITIONS: dict[str, frozenset[str]] = {
    NOT_STARTED: frozenset({PARTIALLY_APPLIED, COMMITTED}),
    PARTIALLY_APPLIED: frozenset({COMMITTED, ROLLED_BACK}),
    ROLLED_BACK: frozenset({NOT_STARTED, COMMITTED}),
    COMMITTED: frozenset(),
}

#: The primary part, followed by one part per derived surface in a fixed order.
RECORD_PART = "record"


def is_state(value: Any) -> bool:
    return isinstance(value, str) and value in STATES


def valid_transition(before: Any, after: Any) -> bool:
    if not is_state(before) or not is_state(after):
        return False
    return after in TRANSITIONS[before]


def transition_error(before: Any, after: Any) -> str | None:
    """A human-readable reason a transition is illegal, or None when it is legal."""
    if not is_state(before):
        return f"{before!r} is not a write state"
    if not is_state(after):
        return f"{after!r} is not a write state"
    if after in TRANSITIONS[before]:
        return None
    if before == COMMITTED:
        return f"{COMMITTED} is terminal; a second commit duplicates the write"
    return f"{before} -> {after} is not a legal write transition"


def parts(record_id: str, held_surfaces: dict[str, list[dict[str, Any]]]) -> list[str]:
    """The ordered parts of one record write: the record, then each surface copy."""
    ordered = [RECORD_PART]
    for surface in ("index", "cache", "summary"):
        if any(entry.get("record_id") == record_id for entry in held_surfaces.get(surface, [])):
            ordered.append(surface)
    return ordered


def part_boundaries(record_id: str, held_surfaces: dict[str, list[dict[str, Any]]]) -> list[int]:
    """How many parts may be applied while the write is still incomplete."""
    total = len(parts(record_id, held_surfaces))
    return list(range(1, total))


def legal_points(held: Iterable[str],
                 held_surfaces: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Every legal mid-write interruption point, in a fixed order.

    A fixture bounds the set with integer positions into this enumeration, so it
    never names the record or the part itself. The order is: records sorted by
    id, then part boundary ascending.
    """
    points: list[dict[str, Any]] = []
    for record_id in sorted(held):
        for applied in part_boundaries(record_id, held_surfaces):
            points.append({"record": record_id, "after_parts": applied})
    return points


def state_after(applied: int, total: int) -> str:
    """The write state implied by how many of its parts were applied."""
    if applied <= 0:
        return NOT_STARTED
    if applied >= total:
        return COMMITTED
    return PARTIALLY_APPLIED


def applied_parts(record_id: str, applied: int,
                  held_surfaces: dict[str, list[dict[str, Any]]]) -> list[str]:
    return parts(record_id, held_surfaces)[:max(0, applied)]


def describe_partial(record_id: str, applied: int,
                     held_surfaces: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """What a partial write leaves behind, in store terms."""
    ordered = parts(record_id, held_surfaces)
    applied_names = ordered[:max(0, applied)]
    return {
        "record": record_id,
        "state": state_after(applied, len(ordered)),
        "applied": list(applied_names),
        "missing": [name for name in ordered if name not in applied_names],
        "entries": [entry["entry_id"] for surface in applied_names
                    if surface != RECORD_PART
                    for entry in held_surfaces.get(surface, [])
                    if entry.get("record_id") == record_id],
        "record_present": RECORD_PART in applied_names,
    }


def validate_chain(record_id: str, entries: list[dict[str, Any]]) -> list[str]:
    """Check one record's reported write chain; returns the reasons it is invalid.

    A chain must start from ``not_started``, each entry must continue from the
    state the previous one left, every transition must be legal, and nothing may
    follow a terminal state.
    """
    reasons: list[str] = []
    state = NOT_STARTED
    if not entries:
        return [f"{record_id}: no write reported"]
    first = entries[0]
    if first.get("from") != NOT_STARTED:
        reasons.append(f"{record_id}: chain starts from {first.get('from')!r}")
    for index, entry in enumerate(entries):
        before, after = entry.get("from"), entry.get("to")
        if before != state:
            reasons.append(f"{record_id}: entry {index} starts from {before!r} after {state}")
        reason = transition_error(before, after)
        if reason:
            reasons.append(f"{record_id}: entry {index}: {reason}")
        state = after if is_state(after) else state
    return reasons
