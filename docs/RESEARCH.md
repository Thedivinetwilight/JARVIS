# Research atlas (maintained)

Scope: research that directly informed Genesis 0.1 decisions. Retrieved 2026-10-17.
Licences were checked with the GitHub API (`gh api repos/<owner>/<repo> --jq .license.spdx_id`).
No third-party code was copied into JARVIS; all mechanisms were re-implemented.
Classification per Addendum J2: **adopted**, **adapted**, **new integration**,
**hypothesis**, **rejected**.

## Question → evidence → decision

| Design question | Sources consulted | What the evidence says | Decision (classification) |
|---|---|---|---|
| How to structure memory types and the decision loop? | CoALA, Sumers et al. 2023 — https://arxiv.org/html/2309.02427v3 | Taxonomy of working / episodic / semantic / procedural memory; decision cycles with a planning stage (retrieval + reasoning to propose/evaluate) and an execution stage (grounding or learning action). | Memory taxonomy and plan→execute cycle **adapted** (COGNITIVE_ARCHITECTURE §4, §6); the cycle is deterministic code, not a model-driven loop. |
| How to rank memories? | Generative Agents, Park et al. 2023 — https://dl.acm.org/doi/fullHtml/10.1145/3586183.3606763 | Score = weighted recency + importance + relevance, each min-max normalised; recency decays exponentially (0.995 per hour); importance rated by the model; relevance = embedding similarity. | **Adapted**: BM25 relevance + exponential recency + epistemic-status weight + scope; no model-rated importance (non-deterministic) and no embeddings yet. EXP-001 found no measurable benefit of the blend on retrieval_v1 (**hypothesis** kept). |
| How to handle corrections and contradictions? | Zep / Graphiti, Rasmussen et al. 2025 — https://arxiv.org/html/2501.13956v1 ; repo `getzep/graphiti` (Apache-2.0) | Bi-temporal model (valid time vs transaction time); contradicting edges are invalidated (t_invalid set), not deleted; an LLM detects contradictions; new information is prioritised. | Validity interval + supersession instead of deletion **adopted**; contradiction detection by key + source class instead of an LLM (**new integration**); *not* "new wins": different-source conflicts stay open (recency must not decide truth). |
| Which memory-update operations? | Mem0, Chhikara et al. 2025 — https://arxiv.org/html/2504.19413v1 ; repo `mem0ai/mem0` (Apache-2.0) | Extraction phase + update phase where an LLM chooses ADD / UPDATE / DELETE / NOOP against the top-s similar memories. | Operation vocabulary **adapted** (ADD / NOOP / SUPERSEDE / CONFLICT) with deterministic rules; LLM-chosen DELETE of contradicted memories **rejected** (silent loss of history). |
| Where do conversation history and long-term facts live? | MemGPT, Packer et al. 2023 — https://arxiv.org/abs/2310.08560 ; repo `letta-ai/letta` (Apache-2.0) | Main context (system + working context + FIFO) vs external recall storage (full history) and archival storage; the LLM moves data via function calls. | Bounded workspace vs searchable episodic log vs memory items **adapted**; letting the model page its own memory **rejected** for now (the kernel controls writes; models only propose). |
| How to persist execution state and survive interruptions? | LangGraph docs — https://docs.langchain.com/oss/python/langgraph/checkpointers , https://docs.langchain.com/oss/python/langgraph/interrupts ; repo `langchain-ai/langgraph` (MIT) | Checkpoints per super-step and per-task pending writes; on resume a node re-runs from its start, so side effects must be idempotent or wrapped. | Write-ahead `started` records and "never blindly replay side effects" **adopted** in the operation journal (ADR-0006). |
| How to prevent duplicate side effects? | Stripe idempotent requests — https://docs.stripe.com/api/idempotent_requests | Keys map to the saved result of the first request; retries return it; reuse with different parameters errors. | **Adopted** (replay recorded outcome; reject mismatched reuse). |
| Recency/frequency in human memory models? | ACT-R base-level learning (course notes) — https://abrsvn.github.io/files/ACT-R_subsymbolic_3.pdf | B = ln Σ t_k^(−d), decay d usually 0.5; frequency and recency of use raise activation. | Access counts/timestamps are recorded (`touch`) but **not** used in ranking yet (**hypothesis** for EXP-003). |
| What should emotional appraisal assess? | OCC model and Scherer's component process model (USC CSCI534 lecture notes) — https://people.ict.usc.edu/~gratch/CSCI534/Old-Lectures/Lecture2014-03.pdf ; Marsella & Gratch, EMA — https://www.sciencedirect.com/science/article/abs/pii/S1389041708000314 | Structural (OCC: appraisal variables → emotion types) vs process (Scherer: sequential relevance/implication/coping/normative checks) theories. | A small functional subset — explicit statements, inferred cues with uncertainty, urgency, coping (repeated failures) — **new integration**; no claim to implement either theory (ADR-0009). |
| Full-text retrieval without new dependencies? | SQLite FTS5 — https://www.sqlite.org/fts5.html | Porter tokenizer, external-content tables, `bm25()` ranking (more negative = better). | **Adopted** (regular FTS5 tables maintained by triggers; simpler than external-content pitfalls). |
| Can deleted text be removed from disk? | SQLite pragma docs — https://www.sqlite.org/pragma.html ; FTS5 secure-delete (3.42) — https://www.sqlite.org/news.html | `secure_delete` zeroes deleted content; in WAL mode only after checkpoint; FTS5 keeps old index entries until merges unless `secure-delete` (3.42+) is set. | `secure_delete=ON` + FTS `optimize` + `wal_checkpoint(TRUNCATE)` **adopted** for SQLite 3.40 (tested by scanning raw bytes); enable FTS5 `secure-delete` when SQLite ≥3.42 (**roadmap**). |
| How should the Gemini key be sent? | Gemini API reference — https://ai.google.dev/api | Requests include an `x-goog-api-key` header. | **Adopted** (fixes key-in-URL). |
| How to support local models without a proprietary API? | Ollama OpenAI compatibility — https://docs.ollama.com/api/openai-compatibility ; repo `ollama/ollama` (MIT) | `POST http://localhost:11434/v1/chat/completions` with OpenAI-style messages. | **Adopted** as `OpenAICompatibleProvider` (tested against a local fake server; not against a real Ollama install). |
| Voice on Android without new Python dependencies? | `termux/termux-api-package` scripts — https://github.com/termux/termux-api-package/blob/master/scripts/termux-tts-speak.in | `termux-tts-speak` (text via args or stdin) and `termux-speech-to-text` (partial matches to stdout) via the Termux:API app. | Candidate voice adapter (**roadmap R2**), not implemented. |

## Project notes

| Project | Licence (GitHub API) | Source / weights / data | Resource needs | Use in JARVIS |
|---|---|---|---|---|
| Letta (MemGPT) | Apache-2.0 | source available; uses external LLMs | server + DB | ideas adapted; code not used |
| Mem0 | Apache-2.0 | source available; LLM + vector store | embeddings + LLM calls per write | ideas adapted; code not used |
| Graphiti | Apache-2.0 | source available; LLM + graph DB | graph DB (e.g. Neo4j) | ideas adopted; code not used |
| LangGraph | MIT | source available | Python deps | principles adopted; code not used |
| Ollama | MIT | source available; runs open-weight models locally | GPU/CPU + RAM per model | optional provider target |
| llama.cpp | MIT | source available | CPU/GPU | possible provider target (its server API **not verified** in this session) |
| Soar | GitHub reports NOASSERTION | source available | C++ | background only; licence to verify before any use |

## Independent findings vs hypotheses
- Measured here: EXP-001 results (retrieval), crash-recovery behaviour, on-disk erasure
  on SQLite 3.40.1, policy/approval behaviour (tests).
- Reported by sources, not reproduced here: benchmark claims of Mem0, Zep and MemGPT.
- Hypotheses awaiting experiments: value of the ranking blend; semantic retrieval on
  paraphrases (EXP-002); ACT-R-style activation (EXP-003).
