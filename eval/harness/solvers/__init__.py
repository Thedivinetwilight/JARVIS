"""Solver registry. A solver is the system under test for one task."""
from __future__ import annotations

from typing import Callable

from .base import Solver, SolveResult, ToolContext
from .stub import StubSolver

SOLVERS: dict[str, Callable[..., Solver]] = {
    "stub": StubSolver,
}


def build_solver(name: str, mutations: tuple[str, ...] = ()) -> Solver:
    if name not in SOLVERS:
        raise KeyError(f"unknown solver '{name}'; available: {sorted(SOLVERS)}")
    return SOLVERS[name](mutations=mutations)
