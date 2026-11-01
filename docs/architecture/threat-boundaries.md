# Threat boundaries for the local J.A.R.V.I.S. server (`app/`)

Status: describes what `app/jarvis` **actually enforces today**. It is not a plan and it is not a
claim of completeness. Every mitigation below is exercised by a test in `app/tests/`; the test that
covers a statement is named so a reader can check it rather than trust it.

Scope: the local server process, its configuration, its SQLite and audio storage, its HTTP
boundary and the client protocol. Out of scope and unchanged by this work: `eval/` and its
historical evidence, `docs/`, and AETHERION.

Related: `docs/adr/0001-local-first-server-architecture.md`,
`docs/research/09-security-and-permissions.md` (§0 trust model, §5 secrets, §6 egress, §9 audit),
`docs/product/capability-specification.md` §0.2 (deployment and processing boundary).

## 1. Assets, in the owner's terms

| Asset | Where it lives | Why it matters |
| --- | --- | --- |
| Personal task and reminder text | SQLite `tasks`, `reminders` | The owner's day; may contain names and medical or household detail |
| Recorded audio | `<data_dir>/audio/<client>/<session>/chunk-NNNN.bin` | Raw voice is the most sensitive personal data here |
| Client tokens | SQLite `clients.token_hash` | Bearer credentials for the local API |
| Server secret | Environment or a 0600 file only | Derives every token and pairing-code digest |
| Audit trail | SQLite `audit` | Who did what, and what was refused |
| Availability of the local service | the process | A crash loses access to the owner's tasks |

## 2. Trust boundaries

```
 owner's console
   │  (operator token, printed once)
   ▼
 [ CLI: python3 -m app.jarvis ] ──────────────► SQLite + audio files (data_dir, 0700 dirs)
   │                                                   ▲
   │  (same process, no network)                       │
   ▼                                                   │
 [ App.request  ← route → auth → scope → manifest → body → handler → audit ]
   ▲                                                   │
   │  HTTP over loopback (TLS required if not loopback)│
   │                                                   │
 paired device (voice client, token with least-privilege scopes)
   │
   ✗ no outbound path: socket.connect is guarded, deny-by-default
```

Boundaries, and what crosses them:

1. **Device → server (untrusted → trusted).** Everything in an HTTP request is untrusted: method,
   path, query, headers, body, content type and `Content-Length`. The server authenticates, checks
   scope, checks the capability manifest, then bounds the body — in that order.
2. **Server → filesystem (trusted → host).** Only paths derived from server-minted ids are used;
   ids are `token_hex` values, never client-supplied strings, and request paths containing `..` are
   refused (`test_path_traversal_is_refused`).
3. **Server → network (trusted → internet).** Denied unless loopback or an explicitly declared
   destination inside a declared external-service adapter. No adapter exists.
4. **Model/agent plane → nothing.** There is no model, so there is no prompt to inject into, no
   tool-call argument to validate and no reasoning trace to trust. Prompt-injection defences from
   `docs/research/09` §2 are therefore *not applicable yet*, and nothing here should be read as
   having implemented them.

## 3. Adversaries considered

| Adversary | Capability assumed | What stops it |
| --- | --- | --- |
| Another local user or process on the same machine | Can connect to loopback ports, read world-readable files | Bearer token required on every route except `/healthz`, `/readyz`, pairing; secrets are 0600/0700; tokens are stored as HMAC digests (`test_tokens_are_stored_only_as_digests`) |
| A stolen or lost paired device | Holds one device token | Tokens expire (90 days default), scopes are least-privilege per manifest, and the operator can revoke, which stops the token immediately and deletes that client's audio (`test_revoked_token_stops_working_immediately`, `test_revoking_a_device_deletes_its_audio`) |
| A malicious web page in the owner's browser | Can issue cross-origin requests and read some responses | No CORS headers are ever sent; requests still need a bearer token that a browser page cannot obtain (`test_the_server_does_not_answer_requests_from_other_origins`) |
| An attacker who guessed a pairing code | Can try repeatedly | 12 characters from a 32-symbol alphabet, single use, 10-minute default TTL, constant-time comparison, throttled after N failures (`test_repeated_poor_attempts_are_throttled`) |
| A network attacker (if the owner binds beyond loopback) | Can intercept and replay | Non-loopback bind is refused unless TLS is configured (`test_non_loopback_bind_requires_tls`); TLS 1.2 minimum in the adapter |
| A buggy or hostile *client* sending huge or malformed bodies | Can exhaust memory or wedge the server | Body cap before read, per-chunk cap, per-session byte cap, session TTL, empty-chunk refusal (`test_a_body_larger_than_the_limit_is_refused_without_reading_it`, `test_a_session_cannot_exceed_its_byte_budget`) |
| A dependency that phones home | Would try to reach the internet | There are no third-party dependencies; the socket guard blocks the attempt anyway (`test_urllib_request_to_the_internet_is_blocked`) |
| The owner's own mistake | Edits a migration, enables a service, uses a placeholder secret | Checksum mismatch refuses startup-migration (`test_edited_migration_is_refused`); enabling a declared service is refused at startup (`test_enabling_an_external_service_is_refused`); placeholder or short secrets are refused (`test_placeholder_secret_is_refused`) |

**Explicitly not defended against, and stated so nobody assumes otherwise:**

- An attacker who already has the owner's UID (or root). They can read the secret environment, the
  SQLite file and the audio directory. Full-disk encryption and physical control are the owner's
  measures, not the server's.
- A compromised operating system, Python interpreter or a C extension that opens sockets without
  passing through `socket.socket.connect`.
- Denial of service by the local user (the server is single-process; a hostile local process can
  still exhaust its CPU, memory or disk).
- Traffic analysis on a loopback interface by a privileged local process.
- Anything about the speech path beyond storage, because there is no speech path.

## 4. What is *not* in this build, and must not be described as protected

- **No speech recognition, no speech synthesis, no model, no language support.** `/v1/limitations`
  reports 11 stable-coded limitations, including `speech_asr_unavailable`, `speech_tts_unavailable`,
  `no_model_selected`, `language_support_unmeasured`.
- **No text conversation endpoint.** The only text a client may send is a JSON task or a control
  payload. A `text/plain` body on the audio route is refused with `text_input_not_supported`
  (`test_text_bodies_are_refused_with_an_explicit_code`). There is no route that accepts prose.
- **No client application.** No PC, tablet, phone, VR or glasses application exists, so the pairing
  and token flow has no shipped client to attack — and none to protect either.
- **No holographic rendering.** No optics, no display technology, no rendering code. C-13 is a
  boundary only (`surface.holographic` = `boundary-only`), and surfaces are reported with
  `implemented: false`.
- **No web retrieval, telephony or vendor-cloud path.** All four declared services are `disabled`
  with no adapter.
- **No data leaves the machine.** Not because a policy says so, but because there is no code path
  that attempts it and the socket layer refuses if one appeared.

## 5. Auditing

Every request produces exactly one audit record with `allowed`, `denied`, `error` or `info`;
pairing attempts, throttling, revocations and grant creation add their own records. Detail payloads
are scrubbed of tokens, bearer headers and pairing codes before they are stored: the tests assert
that a token, its body and a pairing code never appear in the table
(`test_audit_records_pairing_and_revocation_without_secrets`). Error codes are *not* scrubbed,
because a code like `pairing_code_invalid` is the diagnostic value of the record
(`test_audit_detail_keeps_error_codes_for_diagnosis`).

An audit write failure (for example, readiness probed before the first migration) is reported to the
process log rather than turned into a 500, so an audit problem cannot become an outage — with the
consequence, stated plainly, that such a record exists only in the log.

## 6. Residual risks this build accepts

1. Loopback HTTP in the default configuration is unencrypted. Any local process that can read a
   token can use it until it expires or is revoked.
2. Pairing throttling is in-process memory: a restart clears the counter.
3. Audio is stored unencrypted on disk (published limit), protected by directory permissions.
4. `socket` patching is a Python-level control; a native library would bypass it.
5. The reminder path reports due reminders and never delivers them, so "the owner was reminded" is
   never true — a limitation, not a risk, but it must not be papered over.
6. Deterministic tests establish no statistical rate for anything. They prove behaviour on the
   inputs they use, and nothing about an adversary's success probability.
