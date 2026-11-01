# Phase 9 — Security and Trust Boundaries

- **Status:** published (v0.2, open for review)
- **Published:** 2026-11-01
- **Sources checked:** 2026-11-01
- **Related:** [Index](README.md) · [Phase 4 — tools/verification](04-reasoning-agents-and-orchestration.md) · [Phase 5 — memory](05-memory-and-knowledge.md) · Phase 8 (gates that enforce this document)
- **Evidence labels:** **Established** · **Plausible** · **Unproven** · **`[V]` vendor** · **`[I]` independent** · **`[U]` unverified**

---

## 0. The trust model, stated up front

J.A.R.V.I.S. has one principal (the user), several untrusted input channels, and several tools that
can cause irreversible real-world effects. The model is **not** a trusted component.

```
 TRUSTED                         UNTRUSTED (data, never instructions)
 ─────────                       ────────────────────────────────────
 user's explicit instruction      web pages, emails, PDFs, images, OCR
 application code (policy,        tool outputs (including error strings)
   validation, audit)            third-party API responses
 secret manager                  model-generated tool arguments
                                 model-generated text and "reasoning"
                                 retrieved memory derived from the above
```

**The governing principle:**

> **The model proposes; deterministic code validates, authorises, executes, and records.**
> Model output — including tool calls, arguments, and chain-of-thought — is untrusted input.

Everything in this document is a consequence of that sentence.

---

## 1. Assets, adversaries, and consequences

| Asset | Harm if compromised | Primary adversaries |
|---|---|---|
| Personal data (documents, email, calendar, health) | Exposure, reputational and financial harm | Malicious content in inbound email/web; compromised third-party service |
| Credentials and OAuth grants | Account takeover across services | Prompt injection seeking to exfiltrate; local malware |
| Ability to act (send, pay, delete, book) | Fraud, data loss, social harm | Injected instructions; over-broad permissions |
| Memory store | Persistent manipulation, wrong decisions for months | Poisoned documents; attacker with query access |
| Audit log | Loss of accountability | Anyone with model-mediated write access (must be impossible) |
| Evaluation harness | Gates can be bypassed; results meaningless | Self-modifying system; repo-level access |

---

## 2. Prompt injection

### 2.1 What is established

- **Direct injection** (user-turn attempts to override policy) is a policy problem for the product,
  not a security boundary. Assume it eventually succeeds; do not rely on refusals for safety.
- **Indirect injection** — instructions embedded in web pages, emails, documents, images, or tool
  outputs — is the real threat for an agent with tools. This class was formalised in 2023 as
  compromising real-world LLM-integrated applications `[I]`
  ([arXiv:2302.12173](https://arxiv.org/abs/2302.12173)).
- **Attack success is high.** Agent Security Bench reports prompt-injection style attacks reaching
  **84.30% average attack success** across 13 backbones, with limited defense effectiveness `[I]`
  ([arXiv:2410.02644](https://arxiv.org/abs/2410.02644), ICLR 2025).
- **Model-level robustness claims are vendor-reported.** Google reports Gemini 4 Argon at a **0.7%
  attack success rate across 15 adaptive attempts** on the Gray Swan indirect prompt-injection
  benchmark `[V] [U]` (vendor claim restated by press). Even if accurate, it is a benchmark result,
  not a guarantee for arbitrary documents.

### 2.2 Why prompting cannot be the defence

Three structural reasons:

1. **The channel is the same.** Instructions in retrieved text and instructions from the user arrive
   as tokens in the same context; the model has no cryptographic notion of origin.
2. **Capability and compliance are the same mechanism.** A model that follows instructions well enough
   to be useful follows injected instructions well enough to be dangerous.
3. **It is an arms race with asymmetric cost.** The defender must be right every time; the attacker
   needs one successful document.

### 2.3 The architecture-level defence

**CaMeL** demonstrates the strongest published approach: extract control and data flows from the
*trusted* query, so untrusted retrieved data can never influence program control flow, and use
capabilities to prevent exfiltration over unauthorised data flows. It reports **77% of AgentDojo tasks
solved with provable security vs 84% undefended** `[I]`
([arXiv:2503.18813](https://arxiv.org/abs/2503.18813), v2 abstract; note the earlier v1 reported 67%,
so read the version).

**Design consequence for J.A.R.V.I.S.:** the *plan* is produced from the user's instruction; retrieved
content may only fill typed slots (a date, a name, a line item) that are validated against schemas and
business rules. Retrieved content can never add a step, add a tool, or change a recipient.

### 2.4 Practical rules

1. Label content by provenance in the prompt: `[USER]`, `[DOCUMENT:untrusted]`, `[TOOL:untrusted]`.
2. Strip or neutralise imperative-like content is *not* a defence (do not rely on it), but it reduces
   accidental triggering.
3. Never let a retrieved document specify a URL to fetch, a tool to call, or a payment recipient.
4. Treat tool error strings as untrusted (attackers control third-party services).
5. Rate-limit and log every tool call that transmits data outward.

---

## 3. Tool misuse and excessive permissions

### 3.1 The framework

OWASP's Top 10 for LLM Applications (2025) names **LLM01 Prompt Injection** and **LLM06 Excessive
Agency** as the two most relevant risks for this design, alongside Sensitive Information Disclosure,
Improper Output Handling, and Unbounded Consumption `[I]`/`[U]`
([genai.owasp.org](https://genai.owasp.org/llm-top-10/); a 2026 revision reportedly reorders entries,
including raising Excessive Agency — `[U]`, verify the current edition). MITRE ATLAS and NIST's AI RMF
provide complementary control vocabularies `[U]`.

### 3.2 Permission model (normative)

| Principle | Implementation |
|---|---|
| **Least privilege** | Each tool has its own scope; no tool inherits another's credentials |
| **Per-action, per-scope, per-duration** | Grants look like `calendar.write:work, 90d` — not "calendar access" |
| **Separation of decision and execution** | The agent requests; an executor checks policy, then acts |
| **No credential in context** | Secrets live in a secret manager; the model sees a handle, never a token |
| **Deny by default** | New tools, new scopes, and new data classes are denied until enabled |
| **Bounded autonomy** | Pre-authorisation is limited by count, amount, counterparty, and time |
| **Revocation** | Any grant can be revoked in one action, effective immediately |
| **Audit** | Every authorisation decision is logged with the rule that allowed it |

### 3.3 Risk tiers for actions

| Tier | Examples | Control |
|---|---|---|
| **0 Read** | Search, fetch, list, summarise | Allowed within scope; logged |
| **1 Reversible write** | Create draft, add internal note, create tentative event | Allowed with policy check; undo available; logged |
| **2 Consequential write** | Send email, book travel, modify shared calendar, delete files | **Explicit confirmation** with the exact arguments shown |
| **3 Irreversible / financial / legal** | Payments, transfers, signing, account deletion, permission grants | Confirmation **plus** a second factor (re-entry of a code/amount), hard caps, and no pre-authorisation |

**Established practice:** model-generated arguments must never be the sole basis for a Tier 2/3
action. The confirmation must display the *validated* arguments, not a model paraphrase.

---

## 4. Model-generated arguments are untrusted input

Every tool call passes through a four-stage pipeline:

```
model proposes call
   │
   ├─ 1. SCHEMA validation        types, enums, required fields, ranges
   ├─ 2. BUSINESS validation      allowlisted recipient, amount ≤ cap, date in future,
   │                              timezone explicit, entity matches a confirmed record
   ├─ 3. POLICY check             scope granted, risk tier, approval requirement, rate limits
   └─ 4. EXECUTION                idempotency key, dry-run where supported, result verification
```

Example failure classes this catches (each is a real, common model error):

| Error | Caught by |
|---|---|
| Date off by one due to timezone | Business validation (explicit UTC + local, compare to user's timezone) |
| Recipient hallucinated | Allowlist / contact-record match |
| Amount with wrong magnitude | Range + cap + confirmation |
| Duplicate send on retry | Idempotency key |
| Injection-supplied URL or payment target | Provenance rule (Tier 1 origin check) |
| Tool called outside granted scope | Policy check |

**Established:** none of these validations depends on the model behaving. That is the point.

---

## 5. Secrets and credential handling

| Rule | Rationale |
|---|---|
| Secrets never enter model context | A model that can see a token can be induced to emit it |
| Use short-lived, scoped tokens minted per action | Limits blast radius of a leak |
| Store in OS keychain / secret manager, not in config files | Local malware is a real threat |
| Redact secrets from logs, traces, and evaluation artifacts | Logs are widely readable |
| Rotate on any suspicion, automatically where providers support it | Prompt-injection exfiltration is invisible otherwise |
| Never send credentials to a model provider | Some providers explicitly distinguish "your data" handling; verify per provider `[U]` |

---

## 6. Data leakage and egress control

| Control | Implementation |
|---|---|
| **Classification** | Every record/field tagged `public/personal/sensitive/secret` |
| **Egress allowlist** | Hosted model calls permitted only for classes the user allowed (Phase 1 Q2/Q10) |
| **Minimisation** | Only matched spans and required fields are sent, not whole documents |
| **Egress logging** | Per call: destination, class, record IDs, byte count, purpose |
| **Output filtering** | DLP-style checks on outbound email/messages for secrets, card numbers, ID numbers |
| **Side-channel awareness** | Markdown image URLs, auto-fetched links, and webhook URLs can exfiltrate; resolve links through a proxy with an allowlist |
| **Provider retention setting** | Choose zero-retention/training-off options where offered; record the setting per provider `[U]` |

**Plausible:** most real leaks in personal assistant deployments will be *incidental* (a summariser
including a health fact in an email draft), not adversarial. Output filtering therefore matters as
much as injection defence.

---

## 7. Memory poisoning

Covered mechanically in Phase 5 §8.2. The security requirements:

1. Retrieved content carries a trust label that survives into the write path.
2. Untrusted content can never write to the profile store.
3. Writes that would change consequential behaviour go to a review queue.
4. Periodic integrity audit re-verifies sampled memories against their source spans.
5. **Documented attack:** AgentPoison achieves >80% attack success with <0.1% poison rate and <1%
   benign-performance impact `[I]` ([arXiv:2407.12784](https://arxiv.org/abs/2407.12784)). Any design
   without a write gate should be assumed compromised under this threat model.

---

## 8. Output validation

| Output type | Validation |
|---|---|
| Tool arguments | §4 pipeline |
| Structured JSON | Schema + semantic checks + cross-field consistency |
| Code to execute | Sandbox (container/gVisor/Firecracker-class), no network by default, resource limits, no host mounts |
| Markdown/HTML rendered in UI | Sanitise; block scripts, iframes, remote images (exfiltration channel) |
| URLs to open/fetch | Allowlist or explicit user click; never auto-fetch from model output |
| Shell commands | Allowlisted binaries + argument validation; never `eval` model text |
| File paths | Canonicalise, confine to a workspace root, reject traversal |

**Established:** "improper output handling" is a named OWASP risk because systems routinely pass model
output into interpreters, shells, and browsers `[I]/[U]`.

---

## 9. Audit logs

Record, append-only and tamper-evident (hash chain):

- The user's original instruction (verbatim).
- The plan and every step transition.
- Every tool call: name, validated arguments (redacted where secret), policy decision, result summary.
- Every model call: model ID/version, token counts, cost, data-class egress.
- Every approval: what was shown, what the user chose, when.
- Every memory write: record ID, source, trust level.
- Every retrieved evidence span used in a user-facing claim.

**User-facing requirement:** "Why did you do that?" must resolve to a readable trace — instruction →
plan step → tool call → evidence → outcome. If the trace cannot explain an action, the action should
not have been allowed.

---

## 10. Human approval design

Approval fatigue is a real failure mode (Phase 1 Q6): if the user is asked too often, they will
approve reflexively, and then approval is theatre.

| Mechanism | Use |
|---|---|
| **Show exact arguments** | Always; never a paraphrase |
| **One-screen summary** | Who/what/when/how much, with an undo statement |
| **Bounded pre-authorisation** | "Send to these 5 contacts, ≤3/day, until Friday" — precise and revocable |
| **Batch approvals** | Group low-risk items so the user can review quickly |
| **Escalating friction by risk tier** | Tier 1 = undo notice; Tier 2 = confirm; Tier 3 = confirm + second factor |
| **Silence = no** | Timeouts deny, never approve |
| **Approval limits are not editable by the model** | The system cannot widen its own authority |

---

## 11. Adversarial testing (feeds Phase 8's B7 gates)

| Test class | Method | Gate |
|---|---|---|
| Injection corpus | AgentDojo-style tasks adapted to J.A.R.V.I.S. tools `[I]` | ≤5% attack success; 0 consequential actions |
| Poisoned memory | AgentPoison-style insertions `[I]` | ≤5% attack success; 0 profile writes from untrusted sources |
| Tool-argument fuzzing | Random/malformed/hostile arguments against every tool | 0 unvalidated executions |
| Permission escalation attempts | Prompts asking the system to widen scope or bypass approval | 0 |
| Exfiltration attempts | Documents instructing the model to include data in URLs/emails | 0 egress to non-allowlisted destinations |
| Secret canaries | Planted fake secrets; scan all logs/prompts/outputs | 0 appearances |
| Approval bypass | Scripted "just do it" pressure across tiers | 0 Tier 2/3 actions without approval |
| Sandbox escape attempts | Code-execution tasks trying network/host access | 0 escapes |

**Established:** these tests must run **every release** (Phase 8 §7), because model updates change
behaviour and can silently weaken the system's resistance.

---

## 12. Reasoning-trace monitoring: do not rely on it

The most important 2026 safety finding for an agent builder: OpenAI's GPT-6 Astra system card reports
that the model's internal traces are less legible than its predecessor's, that prompted sandbagging
reduced a monitor's recall to zero on one benchmark, and frames this as higher capacity for covert
sandbagging `[V]` ([system card](https://deploymentsafety.openai.com/gpt-6-astra)).

**Consequences for J.A.R.V.I.S.:**

1. Do not treat chain-of-thought as an audit record. Audit **actions and evidence**, not reasoning.
2. Any "here's why I did that" explanation is a **reconstruction** and must be labelled as such unless
   it is derived from the actual tool/evidence trace.
3. Prefer models/configurations where action-level verification is possible; treat improved
   monitorability as a feature to verify, not assume.
4. Expect this to worsen at the frontier, not improve — architectural choices that are good for
   capability (recurrence, latent reasoning) are bad for transparency.

---

## 13. Reference control set (implementable checklist)

| # | Control | Priority |
|---|---|---|
| 1 | Provenance labels on all ingested content; untrusted content cannot alter control flow | P0 |
| 2 | Four-stage tool-call pipeline (schema → business → policy → execution) | P0 |
| 3 | Per-tool, per-scope, time-limited grants; deny by default | P0 |
| 4 | Risk-tiered approvals with exact-argument display | P0 |
| 5 | Secrets in keychain/secret manager; never in context or logs | P0 |
| 6 | Egress allowlist + per-call egress log | P0 |
| 7 | Memory write gate with trust levels and review queue | P0 |
| 8 | Sandboxed code execution; sanitised UI output; URL allowlist | P0 |
| 9 | Append-only, hash-chained audit log with user-facing trace | P0 |
| 10 | Adversarial suite in CI, every release | P0 |
| 11 | Idempotency keys on all side-effecting tools | P1 |
| 12 | DLP filter on outbound messages | P1 |
| 13 | Integrity audit job over sampled memories | P1 |
| 14 | Red-team exercises beyond the fixed suite (quarterly) | P2 |
| 15 | Cost/consumption caps (unbounded consumption risk) | P1 |

---

## 14. Findings classification

**Established**
- Indirect prompt injection succeeds at high rates against tool-using agents and is not solved by
  prompting `[I]`.
- Architectural separation of control and data flow (CaMeL-style) provides provable resistance to a
  large class of injections, at a measured utility cost (77% vs 84% in AgentDojo) `[I]`.
- Memory poisoning is practical and cheap (>80% success at <0.1% poison rate) `[I]`.
- Model-generated tool arguments must be validated by application code; this is a design requirement,
  not a mitigation to be evaluated later.
- Reasoning-trace monitoring is not a reliable control for the newest frontier models `[V]`.

**Plausible**
- Risk-tiered approvals with exact-argument display will outperform broad confirmation prompts on both
  safety and approval fatigue.
- Zero-tolerance CI gates (approval bypass, egress, secrets) catch more harm than aggregate quality
  metrics.
- Most real-world leaks will be incidental disclosure rather than targeted exfiltration.

**Unproven**
- That any current benchmark ASR (e.g. 0.7% vendor-reported) transfers to arbitrary personal documents.
- That a CaMeL-style design can be implemented for J.A.R.V.I.S.'s tool set at acceptable utility cost.
- That approval fatigue can be measured and tuned without long-term user studies.
- That deletion pipelines can be made verifiably complete across provider-side caches and derived
  summaries (Phase 5 §11 open question).

---

## 15. What was learned, what remains uncertain, and the next research step

**What was learned.** Security for J.A.R.V.I.S. is an **architecture** property: provenance labels,
control/data-flow separation, validated tool arguments, scoped grants, tiered approvals, and an audit
trail that explains actions. No prompt, guardrail model, or reasoning-trace monitor substitutes for
those. The measurable costs are known (CaMeL's utility delta; approval friction), and the measurable
risks are documented (84.30% ASR in ASB; >80% poisoning success in AgentPoison).

**What remains uncertain.** How much utility a CaMeL-style implementation loses on personal tasks; how
to design approval UX that resists fatigue without becoming unsafe; how to verify deletion across
provider caches; and whether frontier models' declining trace monitorability will force action-only
auditing everywhere (probably yes).

**Next research step.** Phase 10: synthesise Phases 1–9 into one proposed modular architecture, at least
two alternatives with tradeoffs, the decisions supported by evidence, the open decisions and
assumptions, the experiments that could falsify the recommendation, and a staged roadmap — without
locking in model size, training infrastructure, or a custom neural architecture.
