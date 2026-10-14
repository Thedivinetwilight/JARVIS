# Security model and current limits

## Current controls
- Network disabled by default; provider requires both opt-in and API key.
- Local HTTP server refuses non-loopback binding.
- Model-proposed tool calls are never automatically executed by the HTTP API.
- Central policy denies dangerous operations and requires explicit intent for writes.
- Exact tool schemas reject extra or missing arguments.
- File listing resolves paths and checks workspace containment.
- Provider key is not included in routine audit data or error URLs.
- Request body and message lengths are bounded.
- API responses disable caching.

## Known limitations
This is an early single-user local prototype, not a hardened service. The local HTTP API has no authentication and must remain on loopback. SQLite audit rows are append-only by application convention, not cryptographically tamper-proof. No filesystem sandbox can be guaranteed merely by path checks if a hostile local process can change symlinks concurrently. The Gemini API key is placed in the provider URL because that is the provider's supported request form; errors deliberately avoid echoing that URL. Do not enable network mode on an untrusted device.

## Required before wider deployment
1. Authentication and CSRF/origin controls if a browser UI is added.
2. Secret manager or OS credential store.
3. Tamper-evident audit chain and retention policy.
4. Per-tool quotas, timeouts, and cancellation.
5. Dedicated subprocess/container sandbox before adding code execution.
6. Dependency pinning and vulnerability scanning.
7. Security tests for symlink races, malformed requests, and concurrency.
8. Explicit user authorization flow for each class of consequential action.
