# Phase 3F Server-Authoritative Uncertainty Review Ledger Contract

## Status

- Phase: 3F
- Capability after code/CI completion: L2 (contract-ready)
- L3 status: not reached; the migration is intentionally unapplied and no controlled staging evidence exists
- Approval scope: `review_only`
- Execution unlock: prohibited
- Automatic scrape, retry, repair, refetch, DB write or lock release: prohibited

Phase 3F adds a durable, authenticated review ledger and immutable audit events. It is a dry-run of the future approval workflow, not an execution authorization. Even an `approved` record is constrained to `execution_enabled=false` and `lock_release_allowed=false` in the database, server response parser and UI.

## Trust Boundary

Supabase/PostgreSQL is the only authoritative ledger. Browser localStorage remains a correlation cache and Phase 3E draft store; it is never an identity, signature, approval or unlock source.

- Next routes require a server-verified Admin profile.
- `owner_user_id` and decision actor are derived from the verified user UUID, never request input.
- Browser profile updates are column-restricted to `full_name` and `updated_at`; role, tier, billing and quota fields cannot be self-edited.
- The service-role key remains server-side.
- Browser roles receive no table policy and no table/function privilege.
- Missing configuration, missing migration/RPC, database errors and malformed RPC responses fail closed.
- No SQLite or localStorage fallback can produce an authoritative record.

## API Surface

- `POST /api/scrape/uncertainty-review-requests`: create or idempotently recover a review request.
- `GET /api/scrape/uncertainty-review-requests?scope=mine|reviewable&limit=1..100`: bounded list.
- `GET /api/scrape/uncertainty-review-requests/{requestId}`: owner/reviewer-scoped status.
- `POST /api/scrape/uncertainty-review-requests/{requestId}/decision`: CAS transition for `approve`, `reject` or requester-only `revoke`.

All responses use `Cache-Control: no-store`. Request bodies are byte-bounded and strictly allowlisted. Identity, status, hash, expiry, approval and execution fields supplied by a client are rejected.

## Ledger and Event Contract

The unapplied migration `supabase/migrations/20260718_scrape_uncertainty_review_ledger.sql` defines:

- owner-bound, UUID-keyed review requests;
- DB-normalized SHA-256 binding over the complete safety-relevant payload;
- `(owner_user_id, client_request_id)` idempotency;
- advisory-lock and unique-index concurrency control;
- versioned compare-and-swap transitions;
- atomic expiry materialization;
- append-only `created`, `approved`, `rejected`, `revoked` and `expired` events;
- an UPDATE/DELETE rejection trigger on the event table;
- RLS with no browser policy;
- service-role-only `SECURITY DEFINER` RPCs with fixed `search_path`;
- no unlock, execute, consume or reservation RPC.

An independent Admin is required for approve/reject. The requester cannot decide their own request and is the only actor allowed to revoke it. Expiry and revocation remove validity as a review decision, but neither releases the browser lock nor authorizes a scrape.

## UI Contract

The Phase 3E local draft remains unchanged. A second explicit action may submit that strict draft to the server ledger.

- No network mutation occurs when the local Phase 3E draft is recorded.
- Immediately before submission, the durable lock and draft are re-read, strictly parsed and matched.
- The POST body contains only the client request ID, incident snapshot, reason and two safety acknowledgements.
- Only a strict server response is accepted.
- localStorage stores only a request locator after verified readback; the original lock and draft must remain byte-for-byte unchanged.
- Reload performs at most one status GET when a valid locator exists.
- Failed, ambiguous, mismatched, tampered or unsafe responses leave the lock in place and are never automatically retried.
- `pending_review`, `approved`, `rejected`, `revoked`, `expired` and `unknown` all keep execute, Dry-run and retry unavailable.
- The UI exposes no decision or unlock control in Phase 3F.

## Rollback Boundary

Before migration application, application rollback is a normal code rollback and the server routes safely return `503` when the ledger is absent.

After a separately approved staging migration, rollback must preserve audit evidence:

1. disable/hide submission and decision surfaces;
2. revoke RPC execution before application rollback if an incompatibility exists;
3. retain/export request and event tables;
4. never drop or rewrite immutable events as an operational rollback;
5. restore only after schema/RPC version compatibility is proven.

No migration application, deployment or rollback mutation is performed in Phase 3F code integration.

## Phase 3G Boundary

Phase 3G may consider controlled partial unlock only after a separate Supabase reservation/consume design can atomically bind a valid approval to one bounded staging job and prove compensation/rollback behavior. Phase 3F approval records are deliberately unusable as execution tokens.

## Exit Gate

- migration static-contract tests pass and existing migrations are unchanged;
- strict contract and Next route tests pass;
- two-person/CAS/expiry/immutability boundaries are represented by static contract tests; migration compilation and runtime concurrency evidence remain staging work;
- Phase 3F Playwright proves one explicit ledger POST, read-only restoration and zero scrape writes;
- all prior release-blocking E2E suites pass with retries disabled;
- authz matrix counters remain zero;
- secret and test-weakening scanners pass;
- independent audit reports no Critical/High finding;
- L3 remains unclaimed until the migration is explicitly applied in staging and controlled external evidence is captured.
