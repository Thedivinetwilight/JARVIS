# EXP-001 — Memory retrieval strategies

**Question.** Which retrieval mechanism should the kernel use to recall memories for a
natural-language request, given the constraint of no new dependencies?

**Competing approaches.**
1. `v0.2_like_baseline` — v0.2's exact SQL: one `LIKE '%<whole query>%'` over key, value,
   category, newest first (run on a v0.2-schema database with v0.2 upsert semantics).
2. `token_like_fallback` — Genesis fallback when FTS5 is missing: per-token LIKE, weighted
   scoring, plus the ranking blend.
3. `fts5_bm25_relevance_only` — SQLite FTS5, porter stemming, BM25 (key 2.0, value 1.0,
   category 0.5).
4. `fts5_ranked_default` — (3) blended with recency, epistemic status and scope (kernel default).

**Hypotheses.** H1: token-based retrieval greatly outperforms the whole-string LIKE on
natural questions. H2: stemming helps morphological variants. H3: lexical methods fail on
paraphrases. H4: the blend improves ranking over relevance-only.

**Test data.** `evals/retrieval_v1.json` — 38 memory records (35 distinct keys, 3
corrections), 30 queries: 15 lexical, 4 morphological, 3 stale (asking about a corrected
fact), 5 partial paraphrase, 3 paraphrase. The fixture, its relevance labels and the
retrieval code were written by the same developer and committed together in one commit; the
order in which they were written cannot be verified from the repository history. File
SHA-256 is pinned in `evaluation.py` (`2bc43a6d…603dfd`); changing the set requires a new
version.

**Procedure.** `python -m jarvis eval retrieval --out docs/experiments/EXP-001-results.json`.
Memories are inserted oldest-first with a simulated clock; k = 5. Metrics: Recall@5,
MRR@5, Hit@1, empty-result rate, stale-hit rate (a superseded value in the top 5), mean
latency.

**Environment.** Linux x86_64, Python 3.11.2, SQLite 3.40.1. Deterministic (no randomness);
one run is sufficient for reproducibility; latency varies by machine.

**Results (2026-10-17).**

| Strategy | Recall@5 | MRR@5 | Hit@1 | Empty | Stale hits | Latency (ms) |
|---|---|---|---|---|---|---|
| v0.2 LIKE baseline | 0.033 | 0.033 | 0.033 | 0.967 | 0.000 | 0.05 |
| token LIKE fallback | 0.900 | 0.883 | 0.867 | 0.067 | 0.000 | 0.74 |
| FTS5 BM25 (relevance only) | 0.933 | 0.933 | 0.933 | 0.033 | 0.000 | 0.97 |
| FTS5 ranked (default) | 0.933 | 0.933 | 0.933 | 0.033 | 0.000 | 0.95 |

Recall@5 by query type:

| Strategy | lexical | morphological | stale | partial paraphrase | paraphrase |
|---|---|---|---|---|---|
| v0.2 baseline | 0.067 | 0.0 | 0.0 | 0.0 | 0.0 |
| token LIKE | 1.0 | 0.75 | 1.0 | 1.0 | 0.333 |
| FTS5 (both) | 1.0 | 1.0 | 1.0 | 1.0 | 0.333 |

**Findings.**
- H1 supported: the v0.2 search returned nothing for 29 of 30 natural queries.
- H2 supported: "batteries" is missed by token LIKE but found with porter stemming.
- H3 supported: the single paraphrase "success" (q24, "sweetener … drink") matched only
  through the incidental word "take"; genuine paraphrase recall is effectively 0/3.
- H4 **not supported**: the blend and relevance-only produced identical results on this
  set. The blend is kept as an unvalidated design choice (it encodes task scope and
  verification status, which this fixture does not exercise); no improvement is claimed.
- The v0.2 baseline shows zero stale hits only because it overwrote history; it cannot
  answer "what did this used to be?" at all.

**Limitations.** This is a **development set, not independent validation**: it was written
by the developer alongside the implementation, so it shows that retrieval handles the kinds of
query its author anticipated, with a risk of lexical bias toward the stored phrasing. Half of
the queries (15/30) are lexical, so the headline Recall@5 of 0.933 is dominated by queries that
share words with the stored text; on the 3 genuine paraphrases recall is 0/3. Retrieval is
lexical (BM25 over stemmed tokens), not semantic. English only; one memory corpus; latency
measured with 35 active items. No statistical significance is claimed. A claim about general
retrieval quality needs a held-out set written before the system is run on it, ideally by
someone other than the developer (retrieval_v2, ROADMAP R3).

**Reproduction (pre-release audit, 2026-10-17).** The fixture hash matched the pin; a re-run
produced results identical to `EXP-001-results.json` except latency, both before and after the
audit's code fixes; every metric was recomputed independently from the per-query rankings and
matched (details in `docs/PRE_RELEASE_AUDIT.md` §7).

**Decision.** Adopt FTS5/BM25 retrieval with the token-LIKE fallback (ADR-0005). Next
experiment: EXP-002 compares lexical retrieval with a small local embedding model on an
extended set (retrieval_v2) with more paraphrases and scope-sensitive queries, measuring
on-device latency and memory use (ROADMAP R3).
