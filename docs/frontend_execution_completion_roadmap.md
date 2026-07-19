# Frontend Execution Completion Roadmap (Canonicalized for Phase 3A)

## Provenance
- recovered source path: `C:/Users/yuki2/Documents/ws/keiba-ai-pro/docs/frontend_execution_completion_roadmap.md`
- source blob hash: `748f58568af14f5ea9bd6acabfaababd766e6915`
- reconciled base SHA (`origin/develop`): `80556e8ca2fae2280a0d2f5913ed14068d248d8e`
- reconciliation date: `2026-07-12`
- note: This document separates **implemented baseline** and **future execution roadmap**.

---

## 1. Destination State
Target state is a frontend-led scrape operation where operator can:
1. run dry-run and verify safety/readiness,
2. execute scoped scrape safely,
3. monitor job state and completion,
4. inspect fetch summary and data reflection,
5. branch into quality and repair planning flows without accidental write operations.

---

## 2. Implemented Baseline (As-Is)

### 2.1 Completed now
- Dry-run UI and polling on `/data-collection`.
- Execute path on `/data-collection` with progress and completion summary.
- Fetch summary history rendering.
- Data stats / recent races / race detail inspection.
- Refresh Plan preview UI and route (read-only).
- P0 Repair Plan preview UI and route (read-only).
- Execute endpoints for refresh/p0 intentionally return `501 not-implemented`.

### 2.2 Partially complete
- Quality/audit experiences are split across pages/scripts and not fully unified in one dashboard.
- Live validation is now a first-class bounded Admin UI, but controlled staging evidence is still outstanding.

### 2.3 Not complete
- Approval-gated production repair execution with staged release controls.
- End-to-end UI orchestration for cache-reparse/refetch diagnostics and actioning.
- Atomic execution reservation/consume/unlock across the Supabase review ledger and SQLite scrape-job persistence.

---

## 3. Quality Gates for "Execution Complete"

### Gate A: Job correctness
- `status=completed` transition is visible and not inferred from missing/error fallback.
- Failure state is explicit (`error`), with actionable retry path.

### Gate B: Observability
- History must include dry-run and execute differentiators.
- Operator can distinguish "not yet complete" from "zero results".

### Gate C: Safety
- No hidden write path in refresh/p0 plan screens.
- Authz checks (premium/admin) remain enforced.
- Preview endpoints reject unsafe path-like inputs.

### Gate D: Quality bridge
- Post-execute operator flow to quality checks is explicit and low-friction.

---

## 4. Phased Roadmap

## Phase 1 (done): Dry-run UX hardening
- Maintain explicit in-progress vs complete rendering.
- Preserve detailed dry-run breakdown cards.

## Phase 2 (done): Small-window execute stabilization
- Execute from `/data-collection` with progress + completion summary.
- Refresh stats/history after completion.

## Phase 3 (in progress): Progress and error semantics
- Improve status messaging consistency across queued/running/completed/error.
- Standardize timeout/retry wording and user actions.

## Phase 4 (in progress): Post-execute quality summary
- Present concise quality status immediately after execute.
- Bridge into missingness/P0 planning in one operator flow.

## Phase 5 (planned): P0 quality dashboard
- Visualize reason/action breakdown (cache-missing, schema-review, domain-allowed, etc.).

## Phase 6 (in progress): Targeted refetch/live validation UI
- Targeted refetch planning is now first-class read-only UI/route.
- Bounded live validation is now a first-class Admin UI backed by a FastAPI service.
- Client input is limited to target/type/count plus explicit confirmation; URLs and filesystem paths remain server-owned.
- The bounded path performs no automatic HTTP retry: at most three selected URLs means at most three outbound attempts.
- Local one-URL evidence confirmed an unchanged main DB and caches, but it is not deployed staging evidence.
- Code and deterministic CI are L2-ready. A controlled staging run with real external HTTP and zero DB mutation evidence is required for L3.

## Phase 7 (in progress): Server-authoritative uncertainty review dry-run
- Phase 3E adds a local, non-authoritative `pending_review` record for jobless monitoring uncertainty.
- Recording/restoring the packet keeps execute, dry-run and retry locked and performs no API write.
- Complete UUIDs, owner-scoped Admin status/history, durable initial persistence and per-owner active-job single-flight harden the underlying job boundary.
- Phase 3F adds an unapplied Supabase ledger migration, immutable events, idempotent request creation, expiry/revocation and two-person review decisions.
- The browser submits only after explicit action and retains the Phase 3E lock for every server status, including `approved`.
- Profile update privileges prevent browser-side Admin/tier escalation; orphaned local evidence and stale locator responses remain fail-closed.
- All Phase 3F decisions are `review_only`; they are not execution tokens.
- Phase 3G adds an Admin-only reviewer queue that records explicit approve/reject decisions without retry, unlock, execution, navigation or local-lock mutation.
- Phase 3G also adds a release-blocking disposable, digest-pinned `postgres:17.6-bookworm` runtime gate (`--network none`) and a strict sanitized-evidence verifier. Only the container runtime is network-isolated; the CI host may pull the immutable image from its registry.
- Synthetic runtime evidence is always `l3_eligible=false`; the external Supabase migration remains unapplied.
- Reservation/consumption and partial unlock are still unimplemented. Supabase review state and SQLite scrape-job creation require an explicit cross-store saga/outbox and compensation design before execution work can start.
- Phase 3G is L2 contract-ready only. Controlled staging migration/evidence is still required for L3.

## Phase 8 (in progress): Cross-store reservation and controlled staging evidence
- Design an idempotent reservation/consume protocol that cannot lose or duplicate execution across Supabase and SQLite.
- Define durable recovery, compensation, timeout, and operator rollback semantics before exposing any unlock control.
- Apply the review-ledger migration only through a separately approved staging operation and capture sanitized non-synthetic evidence.
- Keep approval review-only until both the atomicity design and staging evidence pass independent audit.
- Phase 3H adds a release-blocking production-readiness decision gate. It consumes the same-run Phase 3G evidence and derives a sanitized `not-ready` verdict while saga/outbox, controlled staging evidence and explicit approvals are absent.
- The Phase 3H manifest cannot self-assert a completed prerequisite or upgrade itself to READY/L3. A trusted attestation producer and the non-executable saga/outbox contract must be implemented and independently audited first.
- Phase 3H changes no browser flow, scrape endpoint, worker dispatch, lock or external environment. Its correct current outcome is L2 / Production NOT_READY.
- Develop CI records that NOT_READY assessment as valid evidence, while main/release PRs invoke a separate promotion policy that fails until trusted READY/L3 evidence exists.
- Phase 3I adds the non-executable saga state machine and a deterministic failure-injection gate. It validates immutable owner/job/review-version/request binding, replay conflicts, consume-before-dispatch, modeled fencing/stale-worker rejection, uncertainty outcomes and compensation rules with a guarded `effect_count=0`. It does not yet validate event-carried expected-version ordering, lease ownership, lease renewal or worker progress.
- Phase 3I consumes the same-run Phase 3H decision and cannot promote it. Its expected result remains L2 / `production_ready=false` / `l3_eligible=false`.
- Phase 3I leaves the durable SQLite saga/outbox, lease-owner/version CAS and external reservation/consume boundary unimplemented. The Phase 3J disposable slice below addresses those contracts without connecting the existing direct worker path; downstream fencing, durable multi-process recovery and staging safety still require later proof.
- Phase 3J implements that store and reservation boundary only for tests and a disposable release-blocking CI topology: temporary SQLite plus a digest-pinned, network-isolated PostgreSQL container with no published port or external credentials.
- Its failure matrix verifies atomic rollback, crash/replay, claim race, lease/fencing, stale acknowledgement, ambiguous remote stop, compensation replay, corrupt/unavailable fail-closed handling and the separation of review approval from execution authority.
- Same-run Phase 3H/Phase 3I evidence and exact commit/schema/migration/contract/runtime hashes are mandatory. Operational worker/network/thread/write counters remain zero while disposable DB effects are counted separately.
- Phase 3J is still L2 / Production NOT_READY / `l3_eligible=false`: no API or worker imports the executable runtime, the migration is externally unapplied, and controlled staging/multi-instance/downstream-effect evidence remains future work.
- Phase 3K aligns the frontend build/runtime boundary on Node 24, removes npm Critical/High findings with compatible updates, and adds full plus production-only release-blocking audit evidence.
- Moderate/Low advisories remain explicit when no compatible non-breaking remediation exists. Phase 3K does not connect the Phase 3J runtime or apply any external migration, so the result remains L2 / Production NOT_READY / `l3_eligible=false`.

---

## 5. Do-Not-Do Constraints
1. Do not default to broad full refetch from UI.
2. Do not expose direct DB write behavior from planning screens.
3. Do not mix unresolved source-empty cases into generic refetch actions.
4. Do not remove no-downgrade principles in future execution phases.

---

## 6. Completion Metrics
- M1: zero "pending shown as zero-result" regressions.
- M2: stable small-window execute success with observable completion artifacts.
- M3: post-execute quality review reachable in one operator path.
- M4: P0 classification and validation visible without script-only dependency.
- M5: approval-gated repair scaffold in place before any write unlock.
- M6: synthetic saga failure matrix is complete with zero guard-observed forbidden primitive attempts before any executable saga work begins.
