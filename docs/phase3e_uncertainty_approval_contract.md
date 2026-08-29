# Phase 3E Uncertainty Review Contract

## Status

- Phase: 3E
- Capability level after code/CI completion: L2 (contract-ready)
- L3 status: not reached; no deployed staging evidence exists
- Execution unlock: not included
- Approval authority: not included

Phase 3E records a local `pending_review` packet for a jobless monitoring uncertainty. It does not approve an operation, release an execution lock, retry a scrape, or create a repair/refetch execution path.

## Trust Boundary

The browser record is explicitly non-authoritative:

- `status` is always `pending_review`;
- `authoritative` is always `false`;
- `executionEnabled` is always `false`;
- `lockReleaseAllowed` is always `false`;
- no approved/unlocked/execution-ready state is accepted by the parser;
- unknown fields fail validation;
- recording or restoring a review never calls the scrape API or the reconciliation unlock function.

The local fingerprint only detects accidental mismatch between the lock and review packet. It is not a signature, identity proof, approval token, or authorization mechanism.

## Eligible Lock

Only a strict, jobless Phase 3B uncertainty lock may produce the form:

- version `1`;
- failure kind `monitoring` or `client_stop`;
- no `jobId`;
- strict `YYYY-MM` request range with start not after end;
- boolean `forceRescrape`;
- canonical ISO timestamp;
- no unknown fields.

A malformed lock is retained and the UI fails closed. A job-bound lock remains on the existing status-reconciliation path and never shows the Phase 3E form.

## Review Input and Persistence

- reason: normalized text, 20 through 500 characters, no control characters;
- acknowledgement 1: server state is unverified;
- acknowledgement 2: this record does not unlock or permit retry;
- request ID: browser-generated UUID;
- the durable lock is re-read and strictly validated immediately before the review is written;
- the review and lock are read back and matched after the write;
- any storage/readback failure leaves no accepted pending state and retains the execution lock.

Reload restores only a strict review packet whose fingerprint, failure kind, timestamp and complete request snapshot match the current lock. Tampered, stale, `approved`, execution-enabled, unlock-enabled and unknown-field packets are rejected.

## Cross-Tab and Server Job Safety

- the Data Collection page listens for uncertainty/review storage events and fails closed when another tab writes or corrupts a lock;
- execute, dry-run, period inputs and retry remain disabled while uncertainty is active or storage cannot be trusted;
- FastAPI permits only one queued/running scrape job per verified Admin owner;
- job IDs are complete UUIDs;
- initial job state must be durably persisted before its worker thread starts;
- status/history are Admin-only and owner-scoped;
- legacy jobs without an owner are not returned through owner-scoped history or status.

## Job Persistence Contract

The local job database adds `owner_user_id` and `request_hash` columns idempotently. State updates use an upsert that preserves `created_at`, owner and request hash. A failed initial persistence returns `503`, removes the in-memory placeholder and starts no worker thread.

`owner_user_id` and `request_hash` are internal binding fields and are never exposed in status/history responses.

## Prohibited Transitions

Phase 3E must not implement any of the following:

- `pending_review -> approved`;
- `pending_review -> unlocked`;
- `pending_review -> execution_ready`;
- manual local lock deletion as an approval action;
- automatic scrape/retry after recording or restoring a review;
- DB repair/upsert/refetch execution;
- server-side approval impersonation from localStorage.

## Next Phase Boundary

Before any future approval can release a lock, Phase 3F must introduce a server-authoritative, authenticated and durable review ledger with immutable audit events, owner/request binding, expiry/revocation semantics, concurrency control and explicit staging evidence. Browser localStorage can never become that authority.

## Exit Gate

- contract parser and tamper tests pass;
- job persistence/owner/single-flight tests pass;
- Next status/history Admin proxy tests pass;
- Phase 3E Playwright suite passes with zero scrape writes, status reads for jobless review, unknown APIs or external requests;
- cross-tab lock propagation fails closed;
- combined release-blocking Playwright suite includes Phase 3E;
- authz matrix counters remain zero;
- secret and test-weakening scanners pass;
- no staging or production level is claimed.
