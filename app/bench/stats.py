"""Statistics for the voice benchmark, with the sample-size gates stated up front.

The methodology is pre-registered (it is in ``docs/benchmarks/voice-workload-benchmark.md`` and
echoed into every result document under ``methodology``), because a percentile computed from three
samples and a percentile computed from three hundred look identical once the number is printed.

Rules enforced here, not merely described:

* a percentile is *reported* only when ``n >= MIN_SAMPLES_LATENCY`` for that cell; below that the
  value is returned as ``not_measured`` with reason ``insufficient_samples``;
* a proportion carries a Wilson 95% interval only when ``n >= MIN_SAMPLES_RATE``;
* a mean WER carries a bootstrap interval only when ``n >= MIN_SAMPLES_WER``; a smaller set is
  reported as a point estimate with ``ci95: null`` and the sample count beside it;
* everything returns the sample count, so no caller can print a number without its ``n``.

Nothing here assumes normality, and no test establishes a statistical rate: these helpers describe a
finite set of runs, they do not estimate the behaviour of an adversary or of unseen audio.
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass
from typing import Iterable, Sequence

MIN_SAMPLES_LATENCY = 20
MIN_SAMPLES_RATE = 10
MIN_SAMPLES_WER = 20

#: Bootstrap resamples and the seed derivation, both fixed so a re-run reproduces the interval.
BOOTSTRAP_RESAMPLES = 1000
BOOTSTRAP_SEED = 20261101


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile, ``q`` in ``[0, 1]``; ``0.0`` for an empty sample."""
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    rank = max(0.0, min(1.0, q)) * (len(ordered) - 1)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[low]
    weight = rank - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def mean(values: Iterable[float]) -> float | None:
    values = [float(value) for value in values]
    return sum(values) / len(values) if values else None


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """Wilson score interval for a binomial proportion, or ``None`` when ``n`` is too small."""
    if n < MIN_SAMPLES_RATE:
        return None
    p = successes / n
    denominator = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    margin = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denominator
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def _rng(seed_material: str) -> random.Random:
    digest = hashlib.sha256(f"{BOOTSTRAP_SEED}:{seed_material}".encode("utf-8")).hexdigest()
    return random.Random(int(digest[:16], 16))


def bootstrap_mean_interval(
    values: Sequence[float],
    *,
    label: str,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> tuple[float, float] | None:
    """Percentile bootstrap interval for the mean, or ``None`` below the sample gate."""
    values = [float(value) for value in values]
    if len(values) < MIN_SAMPLES_WER:
        return None
    generator = _rng(label)
    n = len(values)
    means = []
    for _ in range(resamples):
        total = 0.0
        for _ in range(n):
            total += values[generator.randrange(n)]
        means.append(total / n)
    return (percentile(means, 0.025), percentile(means, 0.975))


@dataclass(frozen=True)
class LatencySummary:
    """Latency for one cell: always carries the sample count and the gate outcome."""

    n: int
    p50_ms: float | None
    p95_ms: float | None
    mean_ms: float | None
    minimum_ms: float | None
    maximum_ms: float | None
    not_measured: str | None

    def to_dict(self) -> dict:
        return {
            "n": self.n,
            "p50_ms": self.p50_ms,
            "p95_ms": self.p95_ms,
            "mean_ms": self.mean_ms,
            "min_ms": self.minimum_ms,
            "max_ms": self.maximum_ms,
            "not_measured": self.not_measured,
            "gate": f"n >= {MIN_SAMPLES_LATENCY}",
        }


def summarise_latency(values: Sequence[float], *, label: str = "latency") -> LatencySummary:
    values = [float(value) for value in values if value is not None]
    n = len(values)
    if n == 0:
        return LatencySummary(0, None, None, None, None, None, "no_samples")
    summary = {
        "n": n,
        "p50_ms": round(percentile(values, 0.50), 3),
        "p95_ms": round(percentile(values, 0.95), 3),
        "mean_ms": round(mean(values) or 0.0, 3),
        "minimum_ms": round(min(values), 3),
        "maximum_ms": round(max(values), 3),
        "not_measured": None,
    }
    if n < MIN_SAMPLES_LATENCY:
        # Keep the raw numbers visible but do not present them as percentiles: the distinction
        # is the whole point of the gate.
        return LatencySummary(
            n=n,
            p50_ms=None,
            p95_ms=None,
            mean_ms=summary["mean_ms"],
            minimum_ms=summary["minimum_ms"],
            maximum_ms=summary["maximum_ms"],
            not_measured="insufficient_samples",
        )
    return LatencySummary(n=n, p50_ms=summary["p50_ms"], p95_ms=summary["p95_ms"],
                          mean_ms=summary["mean_ms"], minimum_ms=summary["minimum_ms"],
                          maximum_ms=summary["maximum_ms"], not_measured=None)


def summarise_rate(successes: int, n: int) -> dict:
    """A proportion with its interval only when the gate is met."""
    point = (successes / n) if n else None
    interval = wilson_interval(successes, n) if n else None
    return {
        "successes": successes,
        "n": n,
        "value": round(point, 6) if point is not None else None,
        "ci95": [round(bound, 6) for bound in interval] if interval else None,
        "not_measured": None if interval else ("no_samples" if not n else "insufficient_samples"),
        "gate": f"n >= {MIN_SAMPLES_RATE}",
    }


def summarise_wer(values: Sequence[float], *, label: str = "wer") -> dict:
    """Mean word error rate with a bootstrap interval only above the sample gate."""
    values = [float(value) for value in values if value is not None]
    n = len(values)
    if not n:
        return {"n": 0, "mean": None, "ci95": None, "not_measured": "no_samples",
                "gate": f"n >= {MIN_SAMPLES_WER}"}
    interval = bootstrap_mean_interval(values, label=label)
    return {
        "n": n,
        "mean": round(mean(values) or 0.0, 6),
        "min": round(min(values), 6),
        "max": round(max(values), 6),
        "median": round(percentile(values, 0.5), 6),
        "ci95": [round(bound, 6) for bound in interval] if interval else None,
        "not_measured": None if interval else "insufficient_samples",
        "gate": f"n >= {MIN_SAMPLES_WER}",
    }


def methodology_block() -> dict:
    """The pre-registered methodology, echoed into every result document."""
    return {
        "min_samples_latency": MIN_SAMPLES_LATENCY,
        "min_samples_rate": MIN_SAMPLES_RATE,
        "min_samples_wer": MIN_SAMPLES_WER,
        "percentile_method": "linear interpolation over sorted samples (nearest-rank equivalent at p95)",
        "interval_method": {
            "proportion": "Wilson score, 95%",
            "mean_wer": f"percentile bootstrap, {BOOTSTRAP_RESAMPLES} resamples, seed {BOOTSTRAP_SEED}",
        },
        "rules": [
            "a percentile is reported only when its cell has at least min_samples_latency samples",
            "a proportion carries an interval only when its cell has at least min_samples_rate samples",
            "a mean WER carries an interval only when it is computed from at least min_samples_wer items",
            "every reported number is accompanied by its sample count",
            "false-accept and false-reject style paired rates are out of scope here: this benchmark "
            "does not measure speaker verification",
            "these are descriptive statistics over a fixed fixture set, not a statistical rate for "
            "unseen audio or for an adversary",
        ],
    }
