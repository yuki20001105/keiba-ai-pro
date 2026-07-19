# Phase 3 Staging E2E Baseline (Persisted in Phase 3A)

## 0. Baseline Metadata
- baseline created at: `2026-07-12`
- workspace: `C:/Users/yuki2/Documents/ws/keiba-ai-pro-phase3`
- branch: `feature/phase-3-staging-e2e`
- current HEAD: `80556e8ca2fae2280a0d2f5913ed14068d248d8e`
- `origin/develop`: `80556e8ca2fae2280a0d2f5913ed14068d248d8e`
- `origin/develop..HEAD`: `0`

Progress split (required):
- 全体進捗推定: 65〜70%
- Phase 3 L3/L4 readiness: 30〜35%

---

## 1. Environment Matrix (Observed)

| axis | local | CI | preview | staging | production |
|---|---|---|---|---|---|
| Next runtime | yes | yes | partially documented | partially documented | yes |
| FastAPI runtime | yes | import/static/test verification | outside CI runtime | environment-specific | yes |
| scrape write flags (`NETKEIBA_RACE_WRITE_ENABLED`, `ALLOW_STAGING_WRITE`) | configurable | forced safe in CI policy | not fully evidenced in repo docs | guarded/stub paths observed | guarded by env + code |
| scheduler | configurable | disabled in CI | unspecified | env-controlled | env-controlled |
| auth boundary | Supabase token pass-through | test fixtures and gate tests | env dependent | env dependent | enforced |

Notes:
- Repo contains strong local/CI evidence.
- Preview/staging/prod exact deployment wiring exists only partially in repository artifacts; some details are operational configuration outside repository.

---

## 2. 15 Flow Inventory (UI/Next/FastAPI/DB/Policy)

Legend: L0 not started, L1 isolated, L2 contract-ready, L3 staging-integrated, L4 production-proven.

| # | flow | UI | Next/API | FastAPI/Script | DB/persistence | policy/auth | current level | target |
|---|---|---|---|---|---|---|---|---|
| 1 | login/session bootstrap | implemented | implemented | n/a | Supabase | auth enforced | L3 | L4 |
| 2 | data-collection dry-run start | implemented | `/api/scrape` | `/api/scrape/start` | no write (dry-run) | admin path | L3 | L4 |
| 3 | scrape job polling | implemented | `/api/scrape/status/{id}` | `/api/scrape/status/{id}` | owner-bound job state | Admin + owner scoped | L3 | L4 |
| 4 | scrape execute | implemented | `/api/scrape` | job execution | writes in execute path | admin + safeguards | L2 | L3 |
| 5 | fetch summary history | implemented | `/api/scrape/history` | `/api/scrape/history` | owner-bound persisted summaries | Admin + owner scoped | L3 | L4 |
| 6 | scrape health | implemented | `/api/scrape/health` | `/api/scrape/health` | read-only | guarded | L3 | L4 |
| 7 | refresh plan preview | implemented | `/api/scrape/refresh-plan` | `plan_scrape_refresh.py` | read-only | premium/admin | L3 | L4 |
| 8 | refresh execute | disabled UI | `PUT /api/scrape/refresh-plan` | none (`501`) | none | intentionally blocked | L1 | L3 |
| 9 | p0 repair plan preview | implemented | `/api/scrape/p0-repair-plan` | `plan_p0_scrape_repair.py` | read-only | premium/admin | L3 | L4 |
| 10 | p0 repair execute | disabled UI | `PUT /api/scrape/p0-repair-plan` | none (`501`) | none | intentionally blocked | L1 | L3 |
| 11 | targeted refetch planning | implemented | `/api/scrape/targeted-refetch-plan` | `plan_p0_targeted_refetch.py` | read-only report output | premium/admin | L2 | L3 |
| 12 | live validation | explicit-confirm Admin UI | Next Admin proxy | FastAPI bounded service + `validate_p0_targeted_refetch_live.py` | temporary report only; DB read-only | Admin + SSRF/runtime bounds | L2 | L3 |
| 13 | prediction + quota consume | implemented | Next proxy routes | FastAPI + quota deps | Supabase RPC + logs | authz + quota contract | L2 | L4 |
| 14 | purchase history write/read | implemented | Next + direct Supabase paths | mixed | Supabase tables | legacy policy documented | L2 | L4 |
| 15 | production-readiness orchestration | implemented | `/api/production-readiness` | allowlisted python/git checks | reports + status | premium/admin | L2 | L3 |

---

## 3. L3/L4 Gap Summary

### Gaps to L3
1. Refresh and P0 execution paths intentionally remain disabled (`501`).
2. Live validation is first-class and contract-ready, but controlled staging evidence has not yet been acquired.
3. Multi-step operator handoff between execute and quality actions is still fragmented.
4. Phase 3G validates the review ledger only in disposable synthetic PostgreSQL. The external Supabase migration is unapplied, and no atomic reservation/consume bridge exists between the Supabase ledger and SQLite scrape jobs.

### Gaps to L4
1. End-to-end staging evidence for all critical write-sensitive flows is incomplete.
2. Approval-based staged rollout policy for repair execution is not yet active.
3. Full production proof artifacts for each high-risk flow are not yet consolidated.

---

## 4. Phase 3A-3H Plan

### Phase 3A (this step)
- Restore and reconcile missing docs from source worktree.
- Persist this baseline with explicit gap inventory.

### Phase 3B
- Consolidate post-execute quality bridge in UI.
- Normalize operator messaging for pending/error/complete semantics.

### Phase 3C
- Add first-class targeted refetch planning UI (still read-only).
- Phase 3C is code/CI ready, but L3 is not reached yet because staging evidence is not acquired.

### Phase 3D
- Add first-class live validation UI (bounded, no DB write).
- Implemented as an Admin-only Next proxy to a FastAPI service; the browser cannot provide URLs, report paths, cache paths, or fixture inputs.
- Import-time SQLite writes, redirect following, unbounded body reads and unbounded total runtime are prohibited by contract and regression tests.
- Runtime reports are a read-only server mount, operational DB data is never baked into the image, and missing prerequisites fail closed before any external HTTP request.
- FastAPI and Next independently recompute sample-derived counts/verdicts so contradictory or zero-attempt `pass` evidence is rejected.
- Automatic HTTP retry is disabled; the explicit 1-3 URL bound is also the total outbound-attempt bound.
- Local one-URL evidence on 2026-07-18 showed one HTTP/parse success and unchanged DB/cache hashes, sizes and mtimes. This is not staging evidence and does not raise the level.
- Phase 3D is code/CI ready at L2. L3 remains unclaimed until a controlled staging run captures external-HTTP evidence and confirms zero DB mutation.

### Phase 3E
- Add a non-executable `pending_review` scaffold for jobless scrape uncertainty.
- The browser record is local and non-authoritative; it never unlocks, retries, approves or calls a write API.
- Strict lock/review parsing, durable readback, cross-tab lock propagation and fail-closed storage handling are enforced.
- Scrape job IDs are complete UUIDs; initial persistence is fail-closed; status/history are Admin-only and owner-scoped; queued/running jobs are single-flight per owner.
- Phase 3E is L2 contract-ready only. A server-authoritative review ledger and controlled staging evidence are required before L3 can be claimed.

### Phase 3F
- Add a Supabase-backed, server-authoritative uncertainty review ledger with immutable audit events.
- Enforce verified Admin ownership, independent-Admin approve/reject, requester-only revoke, expiry, idempotency and versioned CAS entirely inside service-role-only RPCs.
- Restrict authenticated profile UPDATE privileges so browser users cannot self-promote role/tier/billing/quota authority.
- Keep every decision `approval_scope=review_only`, `execution_enabled=false` and `lock_release_allowed=false`; no decision is an execution token.
- Preserve the Phase 3E local draft as a non-authoritative correlation record and add explicit submit/read-only status UI without automatic retry or unlock.
- Treat missing-lock orphan evidence and locator replacement races as blocking evidence, never as unlock proof.
- The migration is committed but intentionally unapplied. Phase 3F is L2 code/CI-ready only; L3 remains unclaimed until explicit staging migration approval and controlled evidence.

### Phase 3G
- Add an Admin-only reviewer console for explicit review-only approve/reject decisions. The console never unlocks, retries, executes, navigates automatically, or mutates the Phase 3E local lock.
- Add a release-blocking disposable PostgreSQL runtime gate using a digest-pinned `postgres:17.6-bookworm` image, `--network none`, no published port, migration replay, concurrency checks, and container cleanup verification. The host image pull is allowed; the container itself has no network.
- Add a strict evidence verifier that correlates commit and migration hashes and rejects unsanitized, stale, malformed, or schema-drifting reports.
- Synthetic runtime evidence is always `l3_eligible=false`. No external Supabase migration is applied by this phase.
- Execution reservation/consume/unlock remains unimplemented. Supabase ledger decisions and SQLite scrape-job creation need an explicit cross-store saga/outbox and compensation design before any controlled unlock can be considered.
- Phase 3G is L2 code/CI-ready only; L3 remains unclaimed.

### Phase 3H
- Production readiness decision gate based on evidence package and explicit approvals.

---

## 5. Required Approvals and Boundaries
- Any move from read-only planning to execution unlock requires explicit approval.
- Supabase RPC and RLS boundaries must remain least-privilege.
- service_role usage is restricted to server-side trusted contexts.
- No external environment mutation (deploy/migration/apply) is in scope for this baseline.

---

## 6. Migration / RLS / RPC Boundary Notes
- Migration assets exist for prediction count and purchase history related contracts.
- Batch consume RPC (`pred_count_batch`) boundary is documented and service-role sensitive.
- RLS assumptions are policy-critical; rollout must include explicit policy verification per environment.
- The Phase 3F review-ledger migration is exercised only in a disposable network-isolated PostgreSQL container during Phase 3G CI. This does not apply it to Supabase or provide staging evidence.

---

## 7. Rollback Sufficiency Assessment
Current rollback posture is partial.
- Strong: code-level guards, execute-disabled endpoints, CI checks.
- Weak: end-to-end rollback runbooks for staged repair execution not fully institutionalized.
Conclusion: rollback controls are **insufficient for broad execution unlock** without Phase 3E-3G completion.

---

## 8. Risk Register (Severity)

| risk | severity | rationale |
|---|---|---|
| accidental write unlock in planning surfaces | High | routes are currently safe, but unlock work is pending |
| cross-store review-to-job atomicity gap | High | authoritative reviews are in Supabase while scrape jobs are in SQLite; no reservation/consume saga exists |
| stale env mismatch across staging/prod | High | repo evidence for non-local envs is partial |
| operator misread of pending vs zero-result | Medium | mitigated in UI, still needs consistency across flows |
| script-only diagnostics fragmentation | Medium | slows incident response and repeatability |
| quota/purchase contract drift | Medium | mixed boundaries across Next/FastAPI/Supabase |

---

## 9. Exit Criteria
1. All high-risk flows reach at least L3 with staging evidence.
2. Targeted refetch/live validation become first-class operator flows.
3. Approval-gated execution scaffold verified with dry-run drills.
4. Rollback playbooks validated for staged unlock scenarios.
5. L3/L4 evidence package is complete and auditable.

---

## 10. Blockers
1. Execution endpoints intentionally disabled by design (required but blocks progress to L3/L4).
2. Live validation is integrated, but server-owned reports/data volumes and staging evidence remain environment-specific prerequisites.
3. Environment-specific deployment proofs partially outside repository.

---

## 11. Technical Debt Snapshot
1. Contract duplication across UI/Next/FastAPI responses.
2. Incomplete unification of quality and remediation operator experience.
3. Planner/validator internals still use fixed scripts behind a typed FastAPI service; future extraction into pure service modules may simplify operations.
