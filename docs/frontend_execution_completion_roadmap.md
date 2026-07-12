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
- Targeted refetch and live validation remain script-oriented operations, not first-class UI flows.

### 2.3 Not complete
- Approval-gated production repair execution with staged release controls.
- End-to-end UI orchestration for cache-reparse/refetch diagnostics and actioning.

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

## Phase 6 (planned): Targeted refetch/live validation UI
- Bring currently script-centric diagnostics into guarded UI workflows.

## Phase 7 (planned): Approval-gated repair execution scaffold
- Keep write-disabled by default.
- Introduce staged unlocking policy with explicit approvals and audit trails.

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
