"""Fixed evaluations. EXP-001: memory retrieval strategies on a pinned test set.

Procedure (reproducible; see docs/experiments/EXP-001-retrieval.md):
1. Load ``evals/retrieval_v1.json`` and verify its SHA-256 against FIXTURE_SHA256, so the
   evaluation set cannot be silently edited to favour a candidate.
2. Insert the same memories, oldest first, with a simulated clock (``age_days``) into:
   - a v0.2-schema database using v0.2's exact upsert and search SQL (the baseline);
   - a Genesis store with FTS5;
   - a Genesis store without FTS5 (the token-LIKE fallback path).
   Items with ``corrects`` are written as corrections (v0.2: overwrite; Genesis: supersede).
3. Run every query through each strategy with k=5 and compute Recall@5, MRR@5, Hit@1,
   empty-result rate, stale-hit rate (a superseded value in the top 5), and latency.
"""
from __future__ import annotations

import hashlib
import json
import platform
import sqlite3
import statistics
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .contracts import MemoryQuery
from .db import Store

FIXTURE_VERSION = "retrieval_v1"
FIXTURE_SHA256 = "2bc43a6dfe5173b55f1e18c1b2b51acfec354c7de1c3d0d26e391d6fb2603dfd"
K = 5

_V02_SCHEMA = """CREATE TABLE memories (
  id INTEGER PRIMARY KEY, category TEXT NOT NULL DEFAULT 'general',
  key TEXT NOT NULL, value TEXT NOT NULL, confidence REAL NOT NULL DEFAULT 1.0,
  source TEXT NOT NULL DEFAULT 'user', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(category, key))"""
_V02_UPSERT = """INSERT INTO memories(category,key,value,confidence,source,created_at,updated_at)
  VALUES(?,?,?,?,?,?,?) ON CONFLICT(category,key) DO UPDATE SET
  value=excluded.value, confidence=excluded.confidence, source=excluded.source, updated_at=excluded.updated_at"""
_V02_SEARCH = ("SELECT * FROM memories WHERE key LIKE ? OR value LIKE ? OR category LIKE ? "
               "ORDER BY updated_at DESC LIMIT ?")


class _Clock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def fixture_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _metrics(results: list[list[str]], queries: list[dict[str, Any]], superseded: set[str],
             latencies: list[float]) -> dict[str, Any]:
    recalls, rrs, hits1, empties, stale = [], [], [], [], []
    for ranked, q in zip(results, queries):
        relevant = set(q["relevant"])
        top = ranked[:K]
        recalls.append(len(relevant & set(top)) / len(relevant))
        rank = next((i for i, sym in enumerate(top, 1) if sym in relevant), None)
        rrs.append(1.0 / rank if rank else 0.0)
        hits1.append(1.0 if top and top[0] in relevant else 0.0)
        empties.append(1.0 if not top else 0.0)
        stale.append(1.0 if any(sym in superseded for sym in top) else 0.0)
    n = len(queries) or 1
    return {"queries": len(queries), "recall_at_5": round(sum(recalls) / n, 3), "mrr_at_5": round(sum(rrs) / n, 3),
            "hit_at_1": round(sum(hits1) / n, 3), "empty_rate": round(sum(empties) / n, 3),
            "stale_hit_rate": round(sum(stale) / n, 3),
            "mean_latency_ms": round(statistics.mean(latencies), 3) if latencies else 0.0}


def run_retrieval_eval(fixture_path: Path) -> dict[str, Any]:
    fixture_path = Path(fixture_path)
    raw = fixture_path.read_bytes()
    data = json.loads(raw)
    sha = hashlib.sha256(raw).hexdigest()
    reference = datetime.fromisoformat(data["reference_time"]).astimezone(timezone.utc)
    memories = sorted(data["memories"], key=lambda m: -m["age_days"])
    queries = data["queries"]
    superseded = {m["corrects"] for m in memories if m.get("corrects")}
    latest_by_key: dict[tuple[str, str], str] = {}
    for m in memories:  # oldest first, so the last write per key wins
        latest_by_key[(m["category"].lower(), m["key"].lower())] = m["id"]

    with tempfile.TemporaryDirectory(prefix="jarvis-eval-") as tmp:
        tmpdir = Path(tmp)
        # --- v0.2 baseline -------------------------------------------------------------
        legacy = sqlite3.connect(tmpdir / "v02.sqlite3")
        legacy.row_factory = sqlite3.Row
        legacy.execute(_V02_SCHEMA)
        for m in memories:
            stamp = (reference - timedelta(days=m["age_days"])).isoformat()
            legacy.execute(_V02_UPSERT, (m["category"], m["key"], m["value"], 1.0, "user", stamp, stamp))
        legacy.commit()

        def legacy_search(text: str) -> list[str]:
            needle = f"%{text.strip()}%"
            rows = legacy.execute(_V02_SEARCH, (needle, needle, needle, K)).fetchall()
            return [latest_by_key[(r["category"].lower(), r["key"].lower())] for r in rows]

        # --- Genesis stores --------------------------------------------------------------
        def build(name: str, use_fts5: bool) -> tuple[Store, dict[int, str]]:
            clock = _Clock(reference)
            store = Store(tmpdir / f"{name}.sqlite3", clock=clock, use_fts5=use_fts5)
            id_to_sym: dict[int, str] = {}
            for m in memories:
                clock.now = reference - timedelta(days=m["age_days"])
                result = store.memory.remember(m["value"], key=m["key"], category=m["category"],
                                               kind=m.get("kind", "fact"), sensitivity=m.get("sensitivity", "normal"),
                                               source="user", correction=bool(m.get("corrects")))
                id_to_sym[result.item["id"]] = m["id"]
            clock.now = reference
            return store, id_to_sym

        fts_store, fts_ids = build("genesis_fts5", True)
        like_store, like_ids = build("genesis_fallback", False)

        def genesis(store: Store, ids: dict[int, str], strategy: str) -> Callable[[str], list[str]]:
            return lambda text: [ids[h.id] for h in store.memory.search(MemoryQuery(text=text, limit=K),
                                                                        strategy=strategy)]

        strategies: dict[str, Callable[[str], list[str]]] = {
            "v0.2_like_baseline": legacy_search,
            "token_like_fallback": genesis(like_store, like_ids, "ranked"),
            "fts5_bm25_relevance_only": genesis(fts_store, fts_ids, "relevance"),
            "fts5_ranked_default": genesis(fts_store, fts_ids, "ranked"),
        }
        report_strategies: dict[str, Any] = {}
        per_query: list[dict[str, Any]] = [{"id": q["id"], "type": q["type"], "text": q["text"],
                                            "relevant": q["relevant"], "results": {}} for q in queries]
        for name, search in strategies.items():
            results, latencies = [], []
            for i, q in enumerate(queries):
                t0 = time.perf_counter()
                ranked = search(q["text"])
                latencies.append((time.perf_counter() - t0) * 1000)
                results.append(ranked)
                per_query[i]["results"][name] = ranked[:K]
            by_type: dict[str, Any] = {}
            for qtype in sorted({q["type"] for q in queries}):
                idx = [i for i, q in enumerate(queries) if q["type"] == qtype]
                by_type[qtype] = _metrics([results[i] for i in idx], [queries[i] for i in idx], superseded,
                                          [latencies[i] for i in idx])
            report_strategies[name] = {"overall": _metrics(results, queries, superseded, latencies),
                                       "by_type": by_type}
        legacy.close()

    summary = {name: s["overall"] for name, s in report_strategies.items()}
    summary["_notes"] = {
        "history_retained_after_correction": {"v0.2_like_baseline": False, "genesis": True},
        "fixture_sha256_matches_pinned": sha == FIXTURE_SHA256,
    }
    return {"experiment": "EXP-001", "fixture": {"path": str(fixture_path), "version": data["version"],
                                                  "sha256": sha, "pinned_sha256": FIXTURE_SHA256,
                                                  "memories": len(memories), "queries": len(queries)},
            "k": K, "environment": {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
                                    "platform": platform.platform()},
            "strategies": report_strategies, "per_query": per_query, "summary": summary}
