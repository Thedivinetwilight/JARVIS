# Phase 5 — Memory and Knowledge

- **Status:** published (v0.2, open for review)
- **Published:** 2026-11-01
- **Sources checked:** 2026-11-01
- **Related:** [Index](README.md) · [Phase 1 — Workload](01-jarvis-workload-and-requirements.md) (T4) · [Phase 4 — Orchestration](04-reasoning-agents-and-orchestration.md) · Phase 9 (security)
- **Evidence labels:** **Established** · **Plausible** · **Unproven** · **`[V]` vendor** · **`[I]` independent** · **`[U]` unverified**

> **Revision R1 (2026-11-01) — status of this document.** Retained as published. §10's memory design stands
> and is **extended** with a fourth store — **procedural memory** (verified skills) — in
> [Phase 12 §3](12-capability-integration-architecture.md) (S-8). Memory writes are now also the first class
> of "evolving intelligence" ([Phase 13](13-evolving-intelligence-update-pipeline.md), U1).

---

## 0. The requirement, stated as a test

From Phase 1, T4: *the right fact, at the right time, with provenance, correctable and deletable.*

Operationally that is five testable properties:

1. **Recall** — when a fact is relevant, it is retrieved (and when irrelevant, it is not).
2. **Update** — a superseded fact does not come back as current.
3. **Abstention** — when we do not know, we say so instead of inventing.
4. **Provenance** — every recalled fact resolves to a source the user can inspect.
5. **Deletion** — after deletion, the fact is not retrievable, anywhere, ever.

Property 5 is the one most memory systems do not test, and it is the one a personal assistant
cannot fail.

---

## 1. Method and evidence quality

Memory research is unusually self-reported. The most-cited 2026 systems publish their own benchmark
numbers, often on benchmarks they also wrote. Concretely:

- **LongMemEval** (the benchmark we rely on most) defines five abilities — information extraction,
  multi-session reasoning, temporal reasoning, knowledge updates, abstention — with 500 curated
  questions embedded in scalable chat histories, and reports ~30% accuracy drops for commercial
  assistants and long-context LLMs across sustained interactions `[I]`
  ([arXiv:2410.10813](https://arxiv.org/abs/2410.10813), 2024-10-14).
- **Mem0** reports (as of its 2026 algorithm update) 94.4 on LongMemEval and 92.5 on LoCoMo with
  6.8K context tokens and ~1.09 s p50 latency `[V]`
  ([repository](https://github.com/mem0ai/mem0); paper
  [arXiv:2504.19413](https://arxiv.org/abs/2504.19413)). These are vendor numbers on a benchmark the
  vendor optimises against, produced by an LLM-based extractor and judged by an LLM.
- **Memory poisoning** is demonstrated, not hypothetical: AgentPoison reports **>80% average attack
  success with <0.1% poison rate and <1% impact on benign performance**, with no model retraining
  `[I]` ([arXiv:2407.12784](https://arxiv.org/abs/2407.12784)).

**Conclusion on evidence:** the *mechanisms* (hybrid retrieval, reranking, provenance records,
bitemporal fields) are Established engineering. The *benchmark scores* for any specific memory
product are Plausible at best. Our design must therefore be validated on our own suite (§10).

---

## 2. The memory problem is really five different stores

Most "memory" failures come from treating one mechanism as a universal store. These are different
problems with different write rules:

| Store | Contents | Write trigger | Read path | Retention | Provenance |
|---|---|---|---|---|---|
| **Working context** | Current turn, tool results, plan state | Every turn | Directly in prompt | Turn/session | Turn IDs |
| **Episodic** | Conversation transcripts, events | Append-only, automatic | Semantic search, recency-weighted | Days–months | Message ID + timestamp |
| **Semantic profile** | Durable facts: preferences, people, constraints, projects | Explicit ("remember this") or high-confidence extraction, **never** from untrusted text | Typed lookup first, then search | Until corrected/deleted | Field-level source |
| **Document corpus** | Files, emails, PDFs, notes | User adds / integration sync | Hybrid retrieval + rerank | User-controlled | Document ID + page/span |
| **Procedural** | Tool definitions, plans, learned routines | Developer-managed; plans per run | Direct injection | Versioned | Version tag |

**Plausible and load-bearing:** keeping these separate prevents the most common corruption mode —
an untrusted web page influencing a durable *profile* fact. Only the profile store has the authority
to change persistent beliefs, and it has the strictest write gate.

---

## 3. Conversation history and context-window use

### 3.1 Why "just use the 1M window" fails

Three independent reasons:

1. **Recall degrades with position and length.** The U-shaped "lost in the middle" result showed
   accuracy highest for content at the start and end of the context and lowest in the middle, with
   middle positions sometimes *below* the closed-book baseline, and degradation growing with context
   length `[I]` ([arXiv:2307.03172](https://arxiv.org/abs/2307.03172), TACL 2024).
2. **Sustained interaction degrades memory.** LongMemEval's headline result is a ~30% drop across
   sessions for assistants and long-context models `[I]`.
3. **Cost and latency are superlinear in practice.** Long prompts cost more per turn, and KV-cache
   engineering (Phase 3 §2.2) exists precisely because long contexts are expensive; even with 1M
   windows, a 200K-token prompt per turn is a budget decision.

Anthropic's production research system reportedly persists its plan to memory when the context
exceeds ~200K tokens because it would otherwise be truncated `[V]`
([multi-agent research system](https://www.anthropic.com/research/multi-agent-research-system)) — an
explicit admission that context is not memory.

### 3.2 Context engineering, not prompt stuffing

**Established practice:** curate the tokens in the window per turn (system instructions, tool
schemas, retrieved evidence, recent turns, plan state) instead of accumulating history. Two
measurable consequences: token cost falls, and mid-context recall failures fall because the relevant
evidence sits near the edges.

**Cost lever worth knowing:** OpenAI's cached-input tier is ~10× cheaper than standard input for the
GPT-6 family `[V]` ([pricing](https://developers.openai.com/api/docs/pricing)). A stable, cached
"core context" prefix plus a small volatile suffix is therefore both cheaper and better-behaved than
a growing transcript.

---

## 4. Structured profile and project records

### 4.1 Why schema-first

Vectors are good at "find things like this". They are bad at "what is the *current* value of X",
"what did the user say in March", and "delete everything related to Y". Those are structured-data
questions, and a personal assistant is asked them constantly.

**Recommendation:** a typed record store as the *authority*, with search as an index over it.

### 4.2 A minimal schema (illustrative)

```yaml
record:
  id: ulid
  subject: user | person:<id> | project:<id> | place:<id>
  predicate: prefers | dislikes | is | has | scheduled | constraint
  value: <typed>            # scalar, enum, date, duration, address
  confidence: 0.0-1.0
  source:                   # provenance, mandatory
    kind: user_statement | user_confirmed | document | integration | inference
    ref: message_id | doc_id#page | event_id
    excerpt_hash: sha256
  observed_at: timestamp    # when the fact became true (valid time)
  recorded_at: timestamp    # when we learned it (transaction time)
  supersedes: record_id?
  sensitivity: public | personal | sensitive | secret
  retention: until_deleted | expires:<date>
  status: active | superseded | deleted
```

The bitemporal pair (`observed_at`, `recorded_at`) is what makes "what did I believe last Tuesday"
and "what was true last Tuesday" answerable — and it is what lets a correction be *shown* rather than
silently overwriting history.

### 4.3 Where the profile comes from

| Source | Trust | Write policy |
|---|---|---|
| User explicitly says "remember…" | highest | Write immediately, mark `user_statement`, echo back what was stored |
| User confirms a proposed fact | highest | Write, mark `user_confirmed` |
| Observed from user's own documents | medium | Propose, require confirmation for `sensitive` |
| Integration data (calendar/email) | medium | Write with `expires` = event end, never generalise |
| Inferred from behaviour | low | Never write durable facts; keep as session-level inference |
| Content from web/third-party text | **none** | **Never** writes to profile (see poison defence, §8) |

---

## 5. Document stores and retrieval-augmented generation

### 5.1 Established components

| Component | What it does | Evidence | Notes |
|---|---|---|---|
| Chunking | Splits documents into retrievable units | Established practice | Structure-aware chunking (sections, tables) beats fixed windows for contracts/manuals |
| BM25 / keyword (FTS) | Exact term match, names, IDs, numbers | Established decades-old IR baseline `[I]` | Still the best single retriever for rare tokens |
| Dense embeddings | Semantic similarity | Established since DPR `[I]` ([arXiv:2004.04906](https://arxiv.org/abs/2004.04906)) | Good paraphrase recall; weak on exact identifiers |
| Late-interaction (ColBERT-style) | Token-level matching with compression | Established `[I]` ([arXiv:2112.01488](https://arxiv.org/abs/2112.01488)) | Higher index cost, better precision |
| Sparse learned (SPLADE-style) | Term expansion in a sparse index | Established `[I]` ([arXiv:2107.05720](https://arxiv.org/abs/2107.05720)) | Fits keyword infrastructure |
| Reranking (cross-encoder / LLM) | Reorders top-k | Established `[I]` | Usually the single biggest precision win per unit cost |
| Metadata filters | Date, author, sensitivity, project | Established | Non-negotiable for privacy scoping |
| Graph-based expansion | Multi-hop relations | Plausible/emerging `[I]` ([GraphRAG, arXiv:2404.16130](https://arxiv.org/abs/2404.16130)) | Expensive to build; use only for multi-hop classes |

The original RAG formulation is Lewis et al., `[I]` ([arXiv:2005.11401](https://arxiv.org/abs/2005.11401)).
It is the right mental model: retrieval supplies *evidence*, the model supplies *reasoning over
evidence* — not a substitute for either.

### 5.2 Vector vs. keyword vs. hybrid

| Approach | Strengths | Weaknesses | Cost/ops | Verdict |
|---|---|---|---|---|
| Keyword (BM25/FTS) | Exact names, IDs, dates, code; no embedding drift; trivial to explain | Misses paraphrase; weak on fuzzy queries | Very low | **Ship first** as the baseline |
| Dense vector | Paraphrase, cross-lingual, semantic | Hallucinated similarity; weak on rare tokens; re-embedding migrations | Medium | Add second |
| Hybrid (fuse + rerank) | Best measured recall in practice | Two indexes; tuning; more latency | Medium | **Recommended target** |
| GraphRAG | Multi-hop, global questions | Build cost, staleness, summary hallucination | High | Defer unless a measured multi-hop gap appears |

**Plausible (needs our own measurement):** hybrid + rerank dominates either alone on personal
document queries, as it does in general IR. **Unproven:** that graph augmentation helps the personal
workload enough to justify its maintenance.

### 5.3 Chunking and provenance

Every chunk must carry `doc_id`, `page`, `char_span`, `section_path`, `created_at`, `author`, and a
`sensitivity` label. Without a span, citation precision (Phase 1 §5.1) cannot be computed, and
"show me where that came from" cannot be answered.

**Established:** page/span-level provenance is cheap to store and expensive to retrofit. Store it from
day one.

---

## 6. Memory write rules

### 6.1 The gate

```
candidate fact
   │
   ├─ from untrusted source? ──► never write to profile (may be indexed as document, labelled untrusted)
   ├─ sensitivity == secret? ──► refuse; secrets belong in a secret store, never in memory
   ├─ duplicate of active fact? ──► bump confidence/observed_at, do not duplicate
   ├─ contradicts active fact? ──► create new record with `supersedes`, keep old as superseded,
   │                               and surface the conflict to the user if impact is high
   ├─ low confidence + inference? ──► keep as ephemeral, do not persist
   └─ else ──► write with provenance + retention; echo confirmation to user
```

### 6.2 Rules worth stating explicitly

1. **Write less than you can.** Every stored fact is a future liability (stale, wrong, sensitive).
2. **Never store secrets.** API keys, passwords, one-time codes, card numbers go to a secret manager
   with different access controls, or are not stored at all.
3. **Never generalise from a single event.** "Was busy Tuesday" ≠ "dislikes meetings".
4. **Echo what you stored.** The user must be able to see and correct it immediately.
5. **Timestamp everything, twice** (valid time and transaction time, §4.2).
6. **Deletion is a state, not a missing row** — mark deleted, then purge, then verify.

---

## 7. Freshness, contradictions, and forgetting

### 7.1 Contradiction policy

| Situation | Behaviour |
|---|---|
| User contradicts memory explicitly | New record supersedes old; old kept as history; confirm to user |
| New observation contradicts memory, low confidence | Do not overwrite; log conflict; ask if consequential |
| Two sources disagree (document vs user statement) | User statement wins for preferences; document wins for document contents; record both |
| Memory contradicts current reality (e.g. old address) | Retrieval returns staleness flag; verifier or user resolves |

### 7.2 Forgetting

Three distinct operations, often conflated:

- **Expiry** (time-based): calendar events, transient states. Cheap, automatic.
- **Supersession** (value changed): keep history, mark inactive.
- **Erasure** (user request or privacy rule): remove content from all stores — primary record,
  full-text index, vector index, caches, derived summaries, logs, and backups (per retention policy).

**Deletion verification test (must ship):** after deletion, run (a) exact-record fetch, (b) keyword
search, (c) semantic search for paraphrases, (d) a summarisation prompt that would surface the fact,
(e) a provenance lookup. All five must return nothing. This test belongs in the regression suite.

---

## 8. Privacy, poisoning, and export

### 8.1 Privacy controls

| Control | Requirement |
|---|---|
| Classification | Every record carries `sensitivity`; policy is applied at retrieval, not at prompt time |
| Access scoping | Retrieval filters by purpose (a "draft an email" task does not see health records) |
| Minimisation | Only the matched spans are sent to a model, not whole documents |
| Egress log | Every model call records which record IDs were included |
| Export | Machine-readable JSONL with provenance, checksummed |
| Correction | User edit creates a new record with `supersedes`; history retained or purged per policy |
| Audit | "Why did you say that?" resolves to record IDs + source spans + retrieval query |

### 8.2 Memory poisoning (the sharpest threat in this phase)

AgentPoison demonstrates that an attacker who can influence a memory or knowledge base can achieve
**>80% attack success with <0.1% poisoned entries**, without retraining the model `[I]`
([arXiv:2407.12784](https://arxiv.org/abs/2407.12784)). The 2026 threat model is worse than
write-access attacks: content the agent merely *reads* (a web page, an email) can become a memory
that is retrieved later in an unrelated session, decoupling injection from damage in time `[U]` for
the specific newer attacks, plausible given the mechanism.

**Defences that follow mechanically:**

1. **Provenance-based trust levels.** Retrieved content carries `trust: untrusted|user|system`; the
   prompt template labels it, and the write gate refuses profile writes from untrusted content.
2. **Never execute instructions found in retrieved content.** Retrieved text is data; the planner
   reads the *user's* instruction only.
3. **Retrieval diversity and anomaly checks**: a single document dominating all queries for a topic
   is suspicious.
4. **Review queue** for any memory write that would change a consequential behaviour (payments,
   contacts, addresses).
5. **Periodic integrity audit**: sample retrievals, verify each against its source span.

### 8.3 Provenance requirements (from Phase 1)

Every recalled fact must be able to answer: *where did this come from, when was it recorded, was it
confirmed by the user, and what changed since?* If a memory design cannot answer that, it fails T4
regardless of benchmark scores.

---

## 9. Memory evaluation and its limitations

### 9.1 What existing benchmarks measure

| Benchmark | Covers | Does not cover |
|---|---|---|
| LongMemEval `[I]` | Extraction, multi-session reasoning, temporal reasoning, updates, abstention | Deletion, provenance correctness, poisoning, privacy scoping |
| LoCoMo `[I]` | Very long conversational memory QA | Same gaps; LLM-judged |
| Vendor suites (e.g. Mem0's reported 94.4) `[V]` | Their own pipeline's strengths | Independent reproduction; adversarial cases |
| AgentPoison / ASB `[I]` | Poisoning/attack success | Benign capability |

### 9.2 Limitations to state plainly

- **Judge dependence.** Most memory benchmarks score with an LLM judge; uncalibrated judges favour
  verbose, confident answers. Our harness must calibrate against human labels (Phase 4 §7.2).
- **Synthetic personas.** LongMemEval uses curated questions in scalable histories; real personal
  data is messier (duplicates, contradictions, OCR noise).
- **No deletion tests.** None of the surveyed benchmarks test erasure.
- **Optimisation pressure.** A vendor's own benchmark numbers are produced while tuning on that
  benchmark; treat as an upper bound.

### 9.3 The J.A.R.V.I.S. memory suite (M-block, to be implemented in Phase 8)

| ID | Test | Pass condition |
|---|---|---|
| M1 | Recall@k on 200 personal facts | ≥95% for k=10 with hybrid retrieval |
| M2 | Knowledge update | ≥90% return the new value; 0% return the superseded value as current |
| M3 | Abstention | ≥90% correct "I don't have that" |
| M4 | Temporal reasoning ("in March") | ≥85% correct |
| M5 | Provenance | 100% of recalled facts resolve to an inspectable source |
| M6 | Deletion | 0 retrievals across all five probes after deletion |
| M7 | Poisoning resistance | <5% attack success on an AgentPoison-style suite |
| M8 | Privacy scoping | 0 cross-class leaks in 50 scripted multi-purpose sessions |

---

## 10. Proposed memory design

```
                    ┌──────────────────────────────────────────┐
   user turn ──────►│ Write gate (trust, sensitivity, dedupe,  │
                    │ contradiction, confirmation echo)        │
                    └───────┬───────────────┬──────────────────┘
                            │               │
                  ┌─────────▼──────┐  ┌─────▼───────────────┐
                  │ Record store   │  │ Document store      │
                  │ (SQLite/PG)    │  │ (files + chunks)    │
                  │ typed, temporal│  │ immutable + spans   │
                  └─────────┬──────┘  └─────┬───────────────┘
                            │               │
                  ┌─────────▼───────────────▼───────────────┐
                  │ Indexes: FTS5 (keyword) + vector +      │
                  │ metadata (sensitivity, time, project)   │
                  └─────────┬───────────────────────────────┘
                            │
   query ─────────► Retriever: filter → hybrid fuse → rerank → top-k
                            │
                            ▼
                  Evidence pack (records + spans + provenance)
                            │
                            ▼
                     Prompt (labelled trust, minimal spans)
```

Design decisions and their justification:

| Decision | Why |
|---|---|
| Single-node SQL (SQLite/Postgres) before a vector database | Personal scale is small; SQL gives transactions, joins, filters, and deletion semantics. Vector DBs are an optimisation, not an architecture. |
| Typed records as authority; search as index | Satisfies update/abstention/deletion, which pure vector stores cannot do cleanly |
| Hybrid retrieval with rerank | Keyword handles names/IDs; dense handles paraphrase; rerank fixes ordering |
| Bitemporal timestamps | Enables "what did I believe then" and honest corrections |
| Trust labels carried through retrieval | Mechanical defence against memory poisoning |
| Deletion as a pipeline with a verification test | Only way to satisfy T4 property 5 |
| Embeddings versioned | Re-embedding migrations are inevitable; store the model ID per vector |

---

## 11. Findings classification

**Established**
- Long-context alone is not memory: sustained-interaction accuracy drops materially `[I]`, and
  position effects persist `[I]`.
- Retrieval quality is improved by combining lexical and semantic signals and by reranking `[I]`.
- Memory stores are an attack surface: >80% attack success at <0.1% poison rate `[I]`.
- Provenance (doc, page, span, timestamp, trust) must be stored at write time `[I]` (design
  necessity, cheap to do early).

**Plausible**
- A typed-record-plus-hybrid-retrieval design meets M1–M8 at personal scale on commodity hardware.
- Vendor memory products are decent retrieval pipelines with optimistic benchmark reporting.
- Separating profile writes from document indexing prevents most poisoning routes.

**Unproven**
- That any published memory benchmark score predicts performance on one user's real data.
- That graph-based memory pays for itself on personal queries.
- That embedding-model upgrades can be done without measurable retrieval regression (drift risk).
- That deletion can be made truly complete across derived summaries and model caches without a
  formal data-flow design (Phase 9 dependency).

---

## 12. What was learned, what remains uncertain, and the next research step

**What was learned.** "Memory" is five stores with different write authorities, not one vector
database. The properties Phase 1 needs — update, abstention, provenance, deletion — are properties of
the *record model*, not of the retriever. Retrieval earns its keep on documents and episodes; durable
personal facts belong in typed records. Poisoning is a mechanical consequence of mixing untrusted
content into durable memory, and it is defeated by write gates and trust labels, not by prompting.

**What remains uncertain.** Whether hybrid retrieval achieves the M1/M4 targets on one person's messy
corpus; how much provenance the user actually wants surfaced (Q12); whether local embeddings are good
enough to keep retrieval inside the privacy boundary; and how a deletion pipeline interacts with
provider-side caching, which no paper addresses.

**Next research step.** Phase 6: with memory, tools, and orchestration defined for text, examine the
modalities that break those assumptions — image/video understanding, speech recognition, speech
generation, streaming interaction, interruption handling, and diarisation — measuring the **complete
voice path** rather than model token speed.
