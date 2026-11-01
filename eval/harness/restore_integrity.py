"""Backup integrity and corruption: one definition shared by loader, runner, solver, scorer.

A backup declares the digest of the contents it was taken with. The fixture stores
intact contents plus a corruption recipe; the harness materialises the corrupted
view before handing the task to a solver, and keeps the recipe out of that view.
A solver can therefore *detect* damage -- the digest it was given no longer matches
what it holds -- but it cannot read which class of damage was planted. The class is
recovered from the data itself, exactly as an implementation would have to do it:

* a record whose metadata is incomplete            -> ``invalid_metadata`` (reject)
* an entry naming a record the backup does not hold -> ``altered_record_id`` (reject)
* a digest mismatch with nothing else wrong         -> ``missing_entry``    (repairable)

The first two must be refused before a restore writes anything, because the backup
no longer describes a state the system can vouch for. A single missing derived copy
is different: the record it describes is present and intact, so the copy can be
re-derived from the live store, and the restore may complete after repairing it.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

#: How each corruption class must be handled. The mapping is a contract: the loader
#: refuses a fixture that declares an expectation contradicting it.
CORRUPTION_CLASSES: dict[str, str] = {
    "missing_entry": "repair",
    "altered_record_id": "reject",
    "invalid_metadata": "reject",
}

#: How a solver reports each class once it has acted on it.
HANDLED: dict[str, str] = {"reject": "rejected", "repair": "repaired"}

#: Handling a backup is allowed to be reported with.
HANDLING = tuple(HANDLED.values())


def canonical(contents: dict[str, Any]) -> str:
    """The exact bytes the digest covers: sorted keys, no incidental whitespace."""
    return json.dumps(contents, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def backup_digest(contents: dict[str, Any]) -> str:
    """sha256 of a backup's canonical contents."""
    return hashlib.sha256(canonical(contents).encode("utf-8")).hexdigest()


def handling_for(corruption: dict[str, Any] | None) -> str | None:
    """The handling a fixture's corruption recipe requires, or None when intact."""
    if not corruption:
        return None
    return CORRUPTION_CLASSES[corruption["type"]]


def expected_handling(corruption: dict[str, Any] | None) -> str | None:
    """The handling a solver must report for this damage, or None when intact."""
    if not corruption:
        return None
    return HANDLED[CORRUPTION_CLASSES[corruption["type"]]]


def integrity_ok(contents: dict[str, Any], declared: Any) -> bool:
    return isinstance(declared, str) and declared == backup_digest(contents)


def apply_corruption(contents: dict[str, Any], corruption: dict[str, Any]) -> dict[str, Any]:
    """The damaged view of a backup, as a restore would find it on disk."""
    damaged = copy.deepcopy(contents)
    kind = corruption["type"]
    if kind == "missing_entry":
        surface = corruption["surface"]
        entry_id = corruption["entry_id"]
        damaged["surfaces"][surface] = [entry for entry in damaged["surfaces"][surface]
                                        if entry.get("entry_id") != entry_id]
    elif kind == "altered_record_id":
        surface = corruption["surface"]
        entry_id = corruption["entry_id"]
        for entry in damaged["surfaces"][surface]:
            if entry.get("entry_id") == entry_id:
                entry["record_id"] = corruption["record_id"]
    elif kind == "invalid_metadata":
        del damaged["record_metadata"][corruption["record_id"]][corruption["field"]]
    else:  # pragma: no cover - validated before it can reach here
        raise ValueError(f"unknown corruption type {kind!r}")
    return damaged


def _metadata_complete(metadata: Any) -> bool:
    from .taskset import METADATA_FIELDS

    if not isinstance(metadata, dict):
        return False
    for field in METADATA_FIELDS:
        value = metadata.get(field)
        if isinstance(value, list):
            if not value:
                return False
        elif not isinstance(value, str) or not value.strip():
            return False
    return True


def detect(contents: dict[str, Any], declared_digest: Any) -> str | None:
    """Classify what is wrong with a backup from its data alone.

    Returns None for an intact backup. The order matters: a structural defect is
    reported as itself even when the digest also disagrees, and an unexplained
    digest mismatch is treated as a missing derived copy, which is the only class
    that can be repaired rather than refused.
    """
    record_metadata = contents.get("record_metadata")
    if isinstance(record_metadata, dict):
        for rid, metadata in record_metadata.items():
            if not _metadata_complete(metadata):
                return "invalid_metadata"
    held = set(contents.get("records") or [])
    for entries in (contents.get("surfaces") or {}).values():
        for entry in entries:
            if entry.get("record_id") not in held:
                return "altered_record_id"
    if not integrity_ok(contents, declared_digest):
        return "missing_entry"
    return None


def missing_entries(contents: dict[str, Any],
                    live_surfaces: dict[str, list[dict[str, Any]]]) -> list[tuple[str, dict[str, Any]]]:
    """Derived copies a damaged backup lost that the live store still holds."""
    damaged_ids = {entry.get("entry_id") for entries in contents["surfaces"].values()
                   for entry in entries}
    repaired: list[tuple[str, dict[str, Any]]] = []
    for surface in ("index", "cache", "summary"):
        for entry in live_surfaces.get(surface, []):
            if entry.get("entry_id") not in damaged_ids:
                repaired.append((surface, copy.deepcopy(entry)))
    return repaired
