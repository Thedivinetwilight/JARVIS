"""Capability registry: an evaluator-owned inventory of what the system can do.

The registry is deliberately boring data. It exists so that breadth is an
inventory with contracts, cost profiles, failure modes and tests — and so that a
fixture cannot silently depend on a capability nobody defined.

**Evaluator boundary.** Everything under `eval/registry/`, `eval/tasks/` and
`eval/baselines/` is owned by the evaluator. Nothing the system under test does
may write to it: a solver receives a task and a tool context, never a registry
handle, and `selftest` asserts that a full run leaves those files byte-identical.
A change to any of them changes the run identity (see `runner.run_block`).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .config import sha256_obj

REGISTRY_PATH = Path("eval/registry/capabilities.json")

ALLOWED_KINDS = {"tool", "skill", "verifier", "policy"}
ALLOWED_IDEMPOTENCY = {"required_key", "none", "natural"}
REQUIRED_FIELDS = ("capability_id", "kind", "purpose", "contract", "cost_profile",
                   "idempotency", "failure_modes", "tests", "evidence", "owner",
                   "families")

#: Files owned by the evaluator. The system under test may read none of them
#: directly and must never write to any of them.
EVALUATOR_OWNED_GLOBS = ("eval/registry/*", "eval/tasks/*/*", "eval/baselines/*")

#: A capability may not claim authority over the evaluator or over security policy.
FORBIDDEN_CAPABILITY_PREFIXES = ("eval.", "harness.", "gate.", "baseline.",
                                 "permission.", "policy.admin")


class RegistryError(ValueError):
    """Raised when the registry is malformed or does not cover the fixtures."""


@dataclass(frozen=True)
class Capability:
    capability_id: str
    kind: str
    purpose: str
    contract: dict[str, Any]
    cost_profile: dict[str, Any]
    idempotency: str
    failure_modes: tuple[str, ...]
    tests: tuple[str, ...]
    evidence: str
    owner: str
    families: tuple[str, ...]

    @property
    def is_model_backed(self) -> bool:
        return not bool(self.cost_profile.get("synthetic", True))


@dataclass(frozen=True)
class Registry:
    path: Path
    version: str
    sha: str
    capabilities: dict[str, Capability]

    def __len__(self) -> int:
        return len(self.capabilities)

    def get(self, capability_id: str) -> Capability:
        try:
            return self.capabilities[capability_id]
        except KeyError as exc:
            raise RegistryError(f"unknown capability {capability_id!r}") from exc

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.capabilities))

    def for_family(self, block: str) -> tuple[str, ...]:
        return tuple(sorted(cid for cid, cap in self.capabilities.items()
                            if block in cap.families))


def registry_sha(path: Path | str = REGISTRY_PATH) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_registry(path: Path | str = REGISTRY_PATH) -> Registry:
    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RegistryError(f"missing registry: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RegistryError(f"invalid JSON in {path}: {exc}") from exc
    if payload.get("synthetic") is not True or payload.get("data_class") != "synthetic":
        raise RegistryError(f"{path}: registry must be marked synthetic")
    if payload.get("evaluator_owned") is not True:
        raise RegistryError(f"{path}: registry must be marked evaluator_owned")

    capabilities: dict[str, Capability] = {}
    for raw in payload.get("capabilities", []):
        missing = [field for field in REQUIRED_FIELDS if field not in raw]
        if missing:
            raise RegistryError(
                f"{path}: capability {raw.get('capability_id')!r} missing {missing}")
        cap = Capability(
            capability_id=str(raw["capability_id"]), kind=str(raw["kind"]),
            purpose=str(raw["purpose"]), contract=dict(raw["contract"]),
            cost_profile=dict(raw["cost_profile"]),
            idempotency=str(raw["idempotency"]),
            failure_modes=tuple(raw["failure_modes"]), tests=tuple(raw["tests"]),
            evidence=str(raw["evidence"]), owner=str(raw["owner"]),
            families=tuple(raw["families"]))
        if cap.capability_id in capabilities:
            raise RegistryError(f"{path}: duplicate capability {cap.capability_id!r}")
        capabilities[cap.capability_id] = cap
    if not capabilities:
        raise RegistryError(f"{path}: no capabilities")
    return Registry(path=path, version=str(payload.get("registry_version", "0")),
                    sha=registry_sha(path), capabilities=capabilities)


def tools_used_by_fixtures(tasksets: Iterable[Any]) -> set[str]:
    """Every capability id the fixtures actually exercise, from the task payloads."""
    used: set[str] = set()
    for taskset in tasksets:
        block = taskset.block
        for task in taskset.tasks:
            if block == "B1":
                used.add("corpus.retrieve")
            if block == "B2":
                used.add(str(task.get("intent", {}).get("tool", "")))
                if task.get("policy", {}).get("requires_approval") or \
                        task.get("policy", {}).get("on_irreversible"):
                    used.add("policy.approve")
            if block == "B3":
                for step in list(task.get("plan", [])) + list(task.get("repair", [])):
                    used.add(str(step.get("tool", "")))
                used.add("state.verify")
            if block == "B8":
                sub = task.get("subfamily")
                used.add({"extraction": "document.extract_fields",
                          "synthesis": "source.reconcile",
                          "planning": "schedule.assign"}.get(sub, ""))
                used.add("state.verify")
            if block == "B9":
                for step in task.get("skill", {}).get("steps", []):
                    used.add(str(step.get("tool", "")))
                used.add("skill.apply")
                used.add("state.verify")
            if block == "B10":
                used.add("memory.consolidate")
                used.add("state.verify")
            if block == "B4":
                used.add("memory.write_record")
                used.add("memory.retrieve_current")
            if block == "B4D":
                used.add("memory.delete_record")
                used.add("memory.rebuild_derived")
            if block == "B16":
                used.add("explain.limitation")
    used.discard("")
    return used


def validate_registry(registry: Registry, tasksets: Iterable[Any] | None = None) -> list[str]:
    """Structural validation plus fixture coverage. Returns problems, never raises."""
    problems: list[str] = []
    if not registry.capabilities:
        problems.append("registry is empty")
    for cap in registry.capabilities.values():
        if cap.kind not in ALLOWED_KINDS:
            problems.append(f"{cap.capability_id}: kind {cap.kind!r} not in {sorted(ALLOWED_KINDS)}")
        if cap.idempotency not in ALLOWED_IDEMPOTENCY:
            problems.append(f"{cap.capability_id}: idempotency {cap.idempotency!r} invalid")
        if any(cap.capability_id.startswith(p) for p in FORBIDDEN_CAPABILITY_PREFIXES):
            problems.append(f"{cap.capability_id}: may not claim evaluator or policy authority")
        if not cap.purpose.strip():
            problems.append(f"{cap.capability_id}: empty purpose")
        for key in ("preconditions", "postconditions", "args_schema"):
            if not cap.contract.get(key):
                problems.append(f"{cap.capability_id}: contract missing {key}")
        if "usd_per_call" not in cap.cost_profile:
            problems.append(f"{cap.capability_id}: cost_profile missing usd_per_call")
        if not cap.cost_profile.get("synthetic", False):
            problems.append(f"{cap.capability_id}: Stage 1 has no model calls; "
                            "cost profile must be synthetic")
        if not cap.tests:
            problems.append(f"{cap.capability_id}: no tests")
        if not cap.failure_modes:
            problems.append(f"{cap.capability_id}: no failure modes")
        if not cap.families:
            problems.append(f"{cap.capability_id}: not attached to any family")
        if not cap.owner.strip():
            problems.append(f"{cap.capability_id}: no owner")
    if tasksets is not None:
        for tool in sorted(tools_used_by_fixtures(tasksets)):
            if tool not in registry.capabilities:
                problems.append(f"fixture uses unregistered capability {tool!r}")
    return problems


def evaluator_digest(root: Path | str = ".") -> str:
    """Digest of every evaluator-owned file; stable across a run if isolation holds."""
    root = Path(root)
    files = []
    for pattern in EVALUATOR_OWNED_GLOBS:
        files.extend(sorted(p for p in root.glob(pattern) if p.is_file()))
    payload = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in files}
    return sha256_obj(payload)
