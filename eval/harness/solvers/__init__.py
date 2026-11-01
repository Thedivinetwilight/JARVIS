"""Solver registry. A solver is the system under test for one task.

Names are stable so that recorded evidence stays interpretable:

  ``stub``           reference profile (Stage 0 default)
  ``stub_naive``     shallow profile: weak retrieval, no conflict detection,
                     greedy planning, recency-only memory, skills ignored
  ``stub_no_verify`` reference logic without read-back verification or citations
  ``oracle_ceiling`` gold-reading probe that bounds the suite (never a system)

None of these is a model. A model plugs in later behind the same `Solver`
protocol (``solvers/base.py``) without changing the harness.
"""
from __future__ import annotations

from typing import Callable

from .base import Solver, SolveResult, ToolContext
from .ladder import LADDER_ORDER, OracleSolver, build_ladder_solver
from .stub import PROFILES, StubSolver


def _reference(mutations: tuple[str, ...] = ()) -> Solver:
    return StubSolver(mutations=mutations, profile="reference")


def _naive(mutations: tuple[str, ...] = ()) -> Solver:
    return StubSolver(mutations=mutations, profile="naive")


def _no_verify(mutations: tuple[str, ...] = ()) -> Solver:
    return StubSolver(mutations=mutations, profile="no_verify")


SOLVERS: dict[str, Callable[..., Solver]] = {
    "stub": _reference,
    "stub_naive": _naive,
    "stub_no_verify": _no_verify,
    "oracle_ceiling": OracleSolver,
}


def build_solver(name: str, mutations: tuple[str, ...] = ()) -> Solver:
    if name not in SOLVERS:
        raise KeyError(f"unknown solver '{name}'; available: {sorted(SOLVERS)}")
    return SOLVERS[name](mutations=mutations)
