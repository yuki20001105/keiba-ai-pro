# Frontend Data Collection System Design (Canonicalized for Phase 3A)

## Provenance
- recovered source path: `C:/Users/yuki2/Documents/ws/keiba-ai-pro/docs/frontend_data_collection_system_design.md`
- source blob hash: `0c53f582b6e26390f04561d31e1c4aa5e91290db`
- reconciled base SHA (`origin/develop`): `80556e8ca2fae2280a0d2f5913ed14068d248d8e`
- reconciliation date: `2026-07-12`
- note: This document separates **implemented (as-is)** from **planned (future)** explicitly.

---

## 1. Scope
This document covers the Data Collection frontend and adjacent read-only planning surfaces.

- UI pages:
  - `src/app/data-collection/page.tsx`
  - `src/app/data-collection/refresh-plan/page.tsx`
  - `src/app/data-collection/p0-repair-plan/page.tsx`
  - `src/app/data-collection/targeted-refetch-plan/page.tsx`
  - `src/app/data-collection/live-validation/page.tsx`
  - `src/app/data-collection/uncertainty-reviews/page.tsx`
- Next API routes:
  - `src/app/api/scrape/*`
  - `src/app/api/data-stats/route.ts`
  - `src/app/api/races/*`
- FastAPI scrape routes:
  - `python-api/routers/scrape.py`
- Related scripts (verified existing):
  - `scripts/plan_scrape_refresh.py`
  - `scripts/plan_p0_scrape_repair.py`
  - `scripts/plan_p0_targeted_refetch.py`
  - `scripts/validate_p0_targeted_refetch_live.py`
  - `scripts/plan_p0_reparse_cache.py`
  - `scripts/diagnose_p0_cache_coverage.py`

Observed mismatch from source document:
- `scripts/diagnose_source_empty_result_cells.py` is not present in current `develop` implementation.

---

## 2. Implemented (As-Is)

### 2.1 Data Collection main page
- Dry-run trigger exists and posts through Next route `/api/scrape` with `dry_run: true`.
- Job polling exists via `/api/scrape/status/{jobId}`.
- Execute path exists via `useBatchScrape()` integration.
- Fetch summary history exists via `/api/scrape/history`.
- Health status probe exists via `/api/scrape/health`.
- Stats and recent races integration exists via `/api/data-stats`, `/api/races/recent`, `/api/races/{race_id}/horses`.
- Links to Refresh Plan and P0 Repair Plan exist from the Data Collection context.

### 2.2 Refresh Plan page and route
- UI is preview-oriented and clearly indicates dry-run intent.
- Next route `src/app/api/scrape/refresh-plan/route.ts`:
  - Authz enforced with `verifyRequestAuth(...requirePremiumOrAdmin...)`.
  - Runs `scripts/plan_scrape_refresh.py` in a child process.
  - Returns `dry_run: true`, `update_enabled: false`.
  - `PUT` responds `501 not-implemented`.
  - Path-like unsafe input keys are rejected (`FORBIDDEN_PATH_KEYS`).

### 2.3 P0 Repair Plan page and route
- UI is preview-only; execute buttons are disabled.
- Next route `src/app/api/scrape/p0-repair-plan/route.ts`:
  - Premium/Admin authz enforced.
  - Runs `scripts/plan_p0_scrape_repair.py`.
  - Returns `dry_run: true`, `read_only: true`, `update_enabled: false`.
  - `PUT` responds `501 not-implemented`.

### 2.4 Targeted Refetch Plan page and route
- UI is preview-only and does not expose execution controls.
- Next route `src/app/api/scrape/targeted-refetch-plan/route.ts`:
  - Authz enforced with `verifyRequestAuth(...requirePremiumOrAdmin...)`.
  - Runs `scripts/plan_p0_targeted_refetch.py`.
  - Returns `dry_run: true`, `read_only: true`, `execution_enabled: false`.
  - Applies fail-closed report validation for numeric fields, URL samples, and safety flags.
  - Rejects unknown/path-like inputs and strips server filesystem paths from responses.

### 2.5 Bounded Live Validation page and route
- UI requires an explicit confirmation before any external HTTP request.
- UI caps validation at 3 sequential URLs and exposes pass/warn/partial/error/busy as separate states.
- Each selected URL permits one outbound attempt with no automatic retry, so one run performs at most 3 external HTTP requests.
- Next route `src/app/api/scrape/live-validation/route.ts`:
  - re-verifies Admin authorization;
  - accepts only `target`, `url_type`, `max_urls`, and literal `confirm_live_fetch=true`;
  - forwards the verified bearer token to FastAPI;
  - validates and allowlist-projects the response;
  - never starts Python and never accepts a URL or filesystem path.
- FastAPI route `POST /api/scrape/live-validation`:
  - independently enforces Admin authorization;
  - runs fixed server-owned planner/validator commands with shell disabled;
  - disables redirects and bounds timeout, body size, output size, concurrency, cooldown and total runtime;
  - treats only non-expired cache rows as available in both planning and validation;
  - requires response-derived horse identity and real pedigree evidence instead of trusting request URLs as parse evidence;
  - recomputes result counts and verdict from validated sample rows before returning evidence;
  - performs no DB repair/upsert/cache write and removes temporary reports on all paths.
- Runtime prerequisites are server-owned report inputs plus read-only data/cache volumes at the documented container paths. Reports mount at `/app/keiba/data/live-validation-inputs:ro`; operational data remains under `/app/keiba/data`. Neither is bundled into the image or Next/Vercel deployment, and missing prerequisites fail closed before external HTTP.

### 2.6 FastAPI scrape contracts relevant to frontend
- `POST /api/scrape/start`: starts one owner-bound async scrape job per Admin, using a complete UUID and durable pre-thread state.
- `GET /api/scrape/status/{job_id}`: Admin-only, owner-scoped job status/progress/result.
- `GET /api/scrape/history`: Admin-only, owner-scoped recent jobs; legacy ownerless rows are hidden.
- `GET /api/scrape/health`: scrape health contract.
- Legacy/specialized paths remain available (not all wired by UI):
  - `POST /api/scrape`
  - `POST /api/rescrape_incomplete`

### 2.7 Jobless uncertainty review and server ledger bridge
- A strict jobless monitoring/client-stop lock can be accompanied by a local `pending_review` packet.
- The packet is explicitly non-authoritative and cannot release the hook lock, enable execute/dry-run/retry, or invoke an API.
- The durable lock is re-read before writing the review and both records are read back and matched.
- Malformed/stale/tampered records fail closed and remain blocked; storage events propagate a new lock to already-open tabs.
- Phase 3F adds an explicit second-step POST to a server-authoritative Supabase ledger and a read-only status refresh. Recording the Phase 3E draft still performs no request.
- Admin identity is server-derived; request/status/hash/expiry/actor fields cannot be injected by the browser.
- Authenticated profile updates are restricted to non-authoritative presentation columns, preventing browser-side role/tier self-promotion.
- Immutable events and versioned RPC transitions support independent-Admin review, requester revoke and expiry, while database constraints fix the scope to `review_only` with execution and lock release disabled.
- A strict local locator is correlation-only. Missing, deleted, malformed or mismatched locator/status data never releases the underlying uncertainty lock.
- Orphaned review/locator evidence without its lock and stale responses from a replaced locator both fail closed.
- The migration is not applied by code integration, so the external environment remains unverified and L3 is unclaimed.

### 2.8 Phase 3G reviewer console and runtime evidence gate
- `/data-collection/uncertainty-reviews` is an Admin-only review surface over the existing server-authoritative ledger APIs.
- Loading is explicit. Only strict pending, unexpired, review-only records are accepted; malformed, duplicate, expired or execution-capable records fail closed.
- Approve/reject requires a review-only acknowledgement, a normalized 20-500 character reason and versioned response correlation.
- The page uses synchronous single-flight guards and performs no scrape write, retry, unlock, automatic navigation or `localStorage` mutation.
- CI exercises the unapplied Phase 3F migration in a disposable digest-pinned `postgres:17.6-bookworm@sha256:f3bd19c606e442c3d7bdfa8002e03fe260a1023351e0ea4598032022b68dd6e3` container with `--network none`, no published port and no external database credentials. The host may contact the image registry to pull that immutable image; the test container itself has no network.
- The runtime contract verifies migration replay, catalog/RLS/RPC boundaries, immutable events, review-only constraints, idempotency, concurrency serialization, expiry and cleanup.
- A strict verifier binds the sanitized report to the tested commit and migration hash and rejects malformed, stale, secret/path-bearing or schema-drifting evidence.
- Synthetic evidence is always `l3_eligible=false`; it proves an L2 runtime contract, not a staging deployment.
- Execution reservation, consume, unlock and execute are not implemented. The Supabase ledger and SQLite scrape jobs require a cross-store saga/outbox and compensation design first.

### 2.9 Phase 3H production readiness decision gate
- A release-blocking offline verifier consumes the same-workflow Phase 3G runtime artifact and a versioned repository manifest.
- The verifier revalidates the Phase 3G schema, freshness, tested commit, migration hash, runtime checks and cleanup before making any readiness decision.
- Missing saga/outbox invariants, staging evidence and explicit migration/unlock/release approvals are converted into deterministic blocker codes.
- Repository input may describe only the current absent prerequisites. It cannot self-assert a completed control, READY or L3; those transitions require a future trusted attestation boundary.
- The sanitized report intentionally returns `verdict=not-ready`, `production_ready=false` and `l3_eligible=false` while still returning a successful contract evaluation.
- No UI, scrape API, worker, lock, Supabase migration or external environment is changed by this gate.

### 2.10 Phase 3I synthetic saga failure-injection gate
- A pure state machine models immutable operation/job/review-version/owner/request-hash binding without connecting the model to an execution path.
- Separate derived review and execution binding hashes are joined by a versioned binding digest; a future adapter must still prove the canonical mapping from current review/job hashes, and review approval is never an execution token.
- The exact pure state model covers reserve/local-prepare/consume/dispatch/running, success, compensation and terminal manual/failure states. Unknown states/events, invalid snapshot versions, invalid state ordering and binding drift fail closed. Event-carried expected-version ordering is not modeled.
- Stable command/event identifiers provide modeled replay idempotency. The model checks a fencing-token floor and stale-token rejection, but does not model a lease owner, renewal/progress events or durable compare-and-swap persistence.
- The release-blocking failure matrix injects modeled cross-store crash windows, response loss, duplicate delivery, deterministic concurrent recovery calls, lease expiry, stale workers, compensation uncertainty and malformed snapshots.
- The Phase 3I job consumes the same-run Phase 3H artifact and emits sanitized synthetic evidence with `effect_count=0`, `production_ready=false` and `l3_eligible=false`. The counter records forbidden effectful primitive attempts observed by the harness. The model has no executable effect adapter, and emitted intents are data rather than effects; the zero count does not cover real multi-instance execution.
- No Data Collection UI, Next/FastAPI scrape API, worker thread, operational database, Supabase migration or external environment is changed. Phase 3I remains L2 contract evidence.

---

## 3. Planned (Future)
- Controlled staging evidence for the bounded live-validation UI and FastAPI service.
- Unified operational dashboard that joins:
  - refresh dry-run
  - p0 repair dry-run
  - targeted refetch dry-run
  - cache/reparse diagnostics
- Controlled, approval-gated execution phase for refresh/p0 repair (currently intentionally disabled).
- A separately approved staging migration/evidence run for the server-authoritative review ledger.
- An executable cross-store implementation (durable saga/outbox, idempotent reservation/consume, durable lease ownership/CAS, downstream fencing and compensation) between the Supabase ledger and SQLite scrape jobs before any lock release is considered. Phase 3I supplies only its synthetic state-machine contract and does not prove durable or multi-instance behavior.

---

## 4. API Contract Matrix (As-Is)

| frontend screen | Next route | backend/script | method | read-only | external HTTP | DB write | status |
|---|---|---|---|---|---|---|---|
| Data Collection | `/api/scrape` | FastAPI `/api/scrape/start` | POST | dry-run yes / execute no | dry-run: no, execute: yes | dry-run: no, execute: yes | implemented |
| Data Collection | `/api/scrape/status/{jobId}` | FastAPI `/api/scrape/status/{job_id}` | GET | yes | no | no | implemented; Admin + owner scoped |
| Data Collection | `/api/scrape/history` | FastAPI `/api/scrape/history` | GET | yes | no | no | implemented; Admin + owner scoped |
| Data Collection | `/api/scrape/health` | FastAPI `/api/scrape/health` | GET | yes | no | no | implemented |
| Refresh Plan | `/api/scrape/refresh-plan` | `plan_scrape_refresh.py` | POST/GET | yes | no | no | implemented |
| Refresh Plan execute | `/api/scrape/refresh-plan` | none | PUT | yes | no | no | disabled (`501`) |
| P0 Repair Plan | `/api/scrape/p0-repair-plan` | `plan_p0_scrape_repair.py` | POST/GET | yes | no | no | implemented |
| P0 Repair execute | `/api/scrape/p0-repair-plan` | none | PUT | yes | no | no | disabled (`501`) |
| Targeted Refetch Plan | `/api/scrape/targeted-refetch-plan` | `plan_p0_targeted_refetch.py` | POST | yes | no | no | implemented |
| Bounded Live Validation | `/api/scrape/live-validation` | FastAPI `/api/scrape/live-validation` + fixed planner/validator | POST | yes | yes (max 3, sequential) | no | implemented, L2 contract-ready |
| Uncertainty Review Queue | `/api/scrape/uncertainty-review-requests?scope=reviewable` | Supabase service-role review RPC | GET | yes | no | no | implemented, Admin review-only, L2 |
| Uncertainty Review Decision | `/api/scrape/uncertainty-review-requests/{requestId}/decision` | Supabase service-role transition RPC | POST | review-only | no | audit ledger only | implemented; no unlock/execution, L2 |

---

## 5. Reconciliation Notes
- Source document intent is preserved.
- All implementation claims were rewritten against current `develop` code.
- Non-existent script reference was corrected to currently existing planning/diagnostic scripts.
- Execution-vs-plan boundary is now explicit to prevent misreading preview UI as write-enabled behavior.
