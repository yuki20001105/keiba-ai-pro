# Project Status and Roadmap

> Status date: 2026-08-02
> Evidence cutoff: repository artifacts through 2026-07-20 plus local validation on 2026-08-02
> Current branch at assessment: `codex/fullstack-readiness`
> Current implementation checkpoint: `3b9836bcb7fe284c379fe92ac47375a2c9a121d1`
> Status: **overall 66% (reasonable range: 64-68%), Production NOT_READY**
> Candidate branch: four clean local commits ahead of `origin/develop`; exact-SHA remote CI is pending

This is the canonical handoff document for answering three questions:

1. What is the project trying to achieve?
2. How much of that goal is demonstrably complete?
3. What must happen, in what order, before the goal is complete?

Do not infer readiness from code presence, a green synthetic test, or an old report. Use the evidence rules in this document.

---

## 0. Phase 3O Naming Clarification

`keiba-ai-pro-phase3o` is the name of this Git worktree directory. As of 2026-08-02, **Phase 3O is not a formally defined or implemented project phase**:

- no Phase 3O specification, workflow, test suite, branch, or commit exists;
- repository phase artifacts currently end at Phase 3N;
- `.git` identifies this directory as a worktree of the parent `keiba-ai-pro` repository;
- the former `fix/phase3n-bootstrap-equivalence` commit `10267de` is already an ancestor of `origin/develop` and was merged by `d9bbcbc`;
- current work now continues from exact `origin/develop` on `codex/fullstack-readiness`.

Therefore, the directory suffix must not be counted as completed Phase 3O work. If the next phase is named Phase 3O, it should first receive an approved scope and exit contract. A reasonable proposed scope is **current-commit evidence reconciliation plus isolated Staging execution**, corresponding to WP0 and WP3 through WP7 below. This proposal is not yet an approved phase definition.

### 0.1 Local worktree environment snapshot

This worktree is suitable for source inspection, frontend development, and focused contract tests. It is not yet a self-contained full-stack runtime.

| Area | Observed state on 2026-08-02 | Assessment |
|---|---|---|
| Git source | Candidate branch `codex/fullstack-readiness` remains based on `origin/develop` commit `d9bbcbc`; all readiness work is local | Correct base; push and exact-SHA CI evidence still require authorization |
| Node runtime | Node 24.12.0, npm 11.6.2; clean `npm ci` and the CI `--omit=optional` dependency-tree check pass | Verified |
| Frontend tests | 21 test files and 245 tests passed | Verified |
| Production build | Next.js 16.2.12 build completed and generated 68 routes | Verified; dependency-origin `url.parse()` warning remains |
| Python runtime | Worktree-local Python 3.11.9 venv exists with CI requirements, pytest, FastAPI, LightGBM, pandas and scikit-learn | Verified |
| Python tests | Full `python-api/tests` suite passes 949 tests; the model builder/verifier slice passes 72 tests and the leakage-sensitive feature-consistency slice passes 73 tests | Locally verified on Python 3.11.9; exact-SHA CI remains pending |
| Local configuration | Ignored `.env` and `.env.local` contain local dummy endpoints and fail-closed write/scheduler/Saga switches; no secrets were copied | Ready for local health/fixture smoke, not authenticated hosted flows |
| Local operational data | A new empty 36 KiB `keiba/data/keiba_ultimate.db` fixture was initialized through the repository storage code | Ready for schema/startup smoke; real scrape/train/predict data remains absent |
| Services and E2E | FastAPI `/health` and Next.js `/api/health` returned 200; public fixture Playwright smoke passed 5/5; services were stopped afterward | Local integration slice verified |
| Dependency security | Next.js 16.2.12, PostCSS 8.5.25 and sharp 0.35.3 compatibility resolution yields 0 Critical/High in full and production audits; 5 production Moderate findings remain through Google Vision/uuid | High release blocker remediated locally; exact-SHA CI confirmation required |
| Model acceptance path | Strict row observations are recomputed into digest-bound evidence; 72 focused builder/verifier tests pass | No current model artifact, real holdout rows, approved business thresholds, or trusted run exists |

Practical readiness:

- source review and frontend/contract-test work: approximately **90% ready**;
- self-contained local smoke execution: approximately **70% ready**;
- isolated Staging/Production operation: governed by the separate 30% operational-proof score below.

The parent worktree assets were not copied. This worktree now has independently generated local-only configuration, a Python 3.11 venv, and an empty schema fixture. Secrets and production data remain absent by design.

The authoritative overall score remains 65.65%/66% until CI produces exact-SHA evidence for the candidate branch. If the repository-quality pillar is rescored from 80% to 88% after that confirmation, the provisional overall value becomes 67.65% (reported as 68%); this provisional value is not a release authorization.

### 0.2 External governance snapshot (read-only audit)

GitHub metadata was inspected without changing repository or provider state on 2026-08-02:

| Boundary | Observed state | Assessment |
|---|---|---|
| Protected approval Environments | `staging-migration`, `staging-execution-unlock`, and `production-release` exist with required reviewer rules and explicit branch policies | Governance skeleton exists |
| Trusted producer | `security/phase3n-trusted-producer-v1` is protected at `5ce4ad7...`; `PHASE3N_TRUSTED_PRODUCER_SHA` points to that revision | Existing producer is stale relative to current gate-critical files and cannot attest this candidate until deliberately updated/re-reviewed |
| Trusted evidence run | Run `29730598574` passed context and migration approval, then failed after waiting at the Staging execution boundary; no successful Phase 3N run exists | No trusted evidence artifact |
| Promotion selector | `PHASE3N_STAGING_EVIDENCE_RUN_ID` is absent | Promotion cannot select an approved run |
| Protected evidence inputs | No Environment secret names were present for the three Phase 3N Environments; the current workflow requires `PHASE3N_STAGING_OBSERVATION_B64` and `MODEL_EVALUATION_OBSERVATIONS_GZIP_B64` at execution unlock | Trusted workflow must fail closed |
| Vercel deployment records | GitHub records contain separate `keiba-ai-pro-staging` Preview/Production deployment Environments; the latest recorded Staging production deployment is `d9bbcbc`, not the current candidate | A distinct Vercel target likely exists, but current-commit deployment and provider identity remain unproven |
| Provider access from this workstation | Vercel and Railway CLIs are installed but unauthenticated; Supabase CLI is absent | Provider metadata and deployed commit cannot be independently verified here |
| Parent local links | Parent worktree has one Vercel link named `keiba-ai-pro` and one generically named Supabase link; this worktree has neither | Link presence does not prove a distinct isolated Staging topology |

This confirms that WP3 is partially scaffolded in GitHub but has not met its exit condition. No provider login, secret write, Environment mutation, branch update, workflow dispatch, deployment, or migration was performed during this audit.

---

## 1. Project Goal

### 1.1 Product goal

Deliver a production-operable horse-racing decision-support system that lets an authorized operator complete this loop safely:

```text
collect race data
  -> validate data quality
  -> generate leakage-safe features
  -> train and evaluate a model
  -> predict win probabilities
  -> generate Kelly-based recommendations
  -> record purchases and results
  -> evaluate ROI, calibration, and model health
  -> improve or roll back the model
```

The system supports betting decisions; it does not guarantee profit.

### 1.2 Engineering goal

The product is complete only when all of the following are true:

- The main operator workflows are usable through the UI, with explicit success and failure states.
- The prediction pipeline preserves INV-01 through INV-08 in `docs/specs/SYSTEM.md`.
- The active model meets the model acceptance contract on an out-of-time evaluation set.
- Write-sensitive flows are proven in an isolated Staging topology, not only in local or synthetic CI.
- Promotion to Production requires trusted, commit-bound evidence and explicit approvals.
- Monitoring, incident handling, and rollback are executable operating procedures.

### 1.3 Required business decisions that are not yet defined

The repository defines an AUC target of at least 0.85, but it does not yet define authoritative acceptance thresholds for:

- out-of-time ROI and minimum bet count;
- maximum drawdown and bankroll risk;
- probability calibration, such as Brier score or expected calibration error;
- prediction latency, availability, and scrape freshness SLOs;
- the observation period required before a model is considered production-proven.

Until these thresholds are approved and encoded, the project can be technically deployable but cannot be called business-goal complete.

---

## 2. Evidence Rules

Use this evidence precedence, highest first:

1. Fresh, trusted, commit-bound Staging or Production evidence.
2. A successful CI artifact bound to the current full commit SHA.
3. A focused test run against the current checkout.
4. Current implementation and contract tests.
5. Design documents and historical reports.

Evidence is **not current proof** when any of these apply:

- it references a different commit;
- it uses placeholder commits such as `111111...`;
- it is synthetic or disposable but the claim requires Staging or Production;
- it is a test fixture rather than a workflow artifact;
- it is stale, malformed, or missing required provenance;
- a document says a phase is implemented but no matching executable check passes.

Current assessment notes:

- `reports/phase3i_saga_failure_injection_gate.json` is successful synthetic L2 evidence, not L3 proof.
- `reports/phase3j_saga_outbox_runtime_gate.json` is successful disposable evidence for commit `5ce4ad7...`, not the current commit.
- `reports/phase3h_production_readiness_gate.json` currently fails and references placeholder commit `111111...`.
- No current `phase3n_staging_evidence_gate.json` exists in `reports/`.
- Therefore the only defensible current production verdict is **NOT_READY**.

---

## 3. Progress Score

The overall percentage is a planning indicator, not a release authorization. It is calculated from four independently scored pillars.

| Pillar | Weight | Current score | Weighted result | Basis |
|---|---:|---:|---:|---|
| Product workflow completeness | 30% | 78% | 23.4% | 6 of 13 workflows are complete UI flows; 7 are partial; none are wholly missing |
| ML and business-value proof | 25% | 65% | 16.3% | Historical AUC 0.8865 exceeds the 0.85 target, but current-commit out-of-time, calibration, ROI, and drawdown proof is incomplete |
| Repository safety and quality gates | 25% | 80% authoritative; 88% provisional | 20.0% authoritative; 22.0% provisional | Auth/fail-closed gates exist; the working tree clears current Critical/High audits and local gates, but exact-SHA CI evidence is pending |
| Staging and Production operational proof | 20% | 30% | 6.0% | Trusted evidence machinery exists, but isolated provider topology, hosted bootstrap, non-synthetic exercises, rollback evidence, and current Phase 3N artifact are unproven |
| **Overall** | **100%** |  | **65.65% authoritative; 67.65% provisional** | Report 66% until candidate commit and CI confirmation; then rescore to 68% if no regression appears |

Workflow scoring assigns 1.0 point to `complete`, 0.6 to `partial`, and 0 to `missing`. Thus $(6 + 7 \times 0.6) / 13 = 78.5\%$, conservatively reported as 78%. Other pillar scores are evidence-based assessments and must be revisited when their exit conditions change.

### 3.1 Interpretation

- **Product implementation:** approximately 78%.
- **Repository-level safety and quality:** approximately 80%.
- **Real-environment readiness:** approximately 30%.
- **Overall goal:** **66%**, with a reasonable uncertainty range of **64-68%**.

The overall score remains close to the 2026-07-12 baseline of 65-70%. Later phases substantially improved safety contracts, but they did not yet close the external Staging evidence and business-validation gaps.

### 3.2 What is complete

- Core architecture: Next.js, FastAPI, SQLite, Supabase boundary, LightGBM pipeline.
- Core UI flows: data collection, data inspection, feature analysis, model training, prediction, and prediction/result analysis.
- Prediction safety invariants and explicit Production fail-closed policy.
- Read-only planning for refresh, P0 repair, targeted refetch, and bounded live validation.
- Review-ledger, saga/outbox, fencing, replay, and failure-injection contracts at synthetic/disposable levels.
- Phase 3M bootstrap manifest and Phase 3N trusted-evidence producer/verifier implementation.
- Release workflow that requires trusted Phase 3N evidence instead of repository self-claims.

### 3.3 What is partial

- Feature generation and advanced model evaluation are not fully operator-visible.
- Model redesign can preview proposals, but the guarded approval-to-retrain-to-activate workflow is incomplete.
- Profiling, smoke suites, and some diagnostics still depend on scripts.
- Operator quality/remediation flow remains fragmented.
- Refresh and P0 repair execution remain intentionally disabled.
- Operational saga code exists, but current non-synthetic multi-instance Staging proof is absent.
- Historical model quality is encouraging, but business acceptance criteria and fresh evidence are incomplete.

### 3.4 What blocks Production

1. Current-commit local and CI evidence has not been regenerated into a coherent evidence set.
2. The Phase 3M bootstrap is not proven applied to an isolated Staging Supabase project.
3. Auth/RLS/IDOR checks are not proven against the real isolated Staging project.
4. Non-synthetic multi-instance crash/recovery and stale-fence rejection are not proven.
5. Database/cache integrity and rollback drill evidence are not proven.
6. The three GitHub Environment approval boundaries are not proven configured and exercised.
7. A fresh trusted Phase 3N artifact for the exact candidate commit does not exist locally.
8. Business success thresholds beyond AUC are not approved. A versioned fail-closed
   contract and verifier now enforce that absence as `not-accepted`; approved values
   and fresh current-commit row observations are still required. Aggregate metrics are
   now recomputed by trusted code and bound to model/source digests.

---

## 4. Definition of Done

The project reaches 100% only when every gate below is satisfied.

### Gate G1: Core product loop

- All 13 workflows in `docs/ui_workflow_completion_matrix.md` are either `complete_ui` or explicitly accepted as an operational script with an owner and runbook.
- No critical workflow has an ambiguous pending, zero-result, or error state.
- Premium/Admin authorization is enforced at both UI and backend boundaries.

### Gate G2: Model acceptance

- A candidate model is evaluated on an out-of-time holdout with no future-field leakage.
- AUC is at least 0.85.
- Calibration, ROI, drawdown, minimum sample size, and comparison-to-baseline thresholds are approved and pass.
- The model bundle, features, data snapshot, metrics, and active-model decision are reproducible and auditable.

### Gate G3: Repository quality

- Frontend tests, focused Python tests, build, invariant checks, dependency audit, and secret/weakening scans pass on the exact candidate commit.
- Generated reports reference the exact full candidate SHA and contain no placeholder or stale evidence.
- INV-01 through INV-08 remain covered by tests or executable checks.

### Gate G4: Isolated Staging

- Vercel/Render/Supabase Staging resources are distinct from Production and bound to the exact candidate.
- Phase 3M bootstrap history and schema fingerprint pass on hosted Staging.
- Free/Premium/Admin auth, RLS, IDOR denial, role-escalation denial, and private-storage denial pass.
- Bounded live validation proves expected external HTTP behavior and unchanged protected DB/cache state.
- At least two real instances pass crash/recovery, no-duplicate-effect, stale-fence, and no-orphan checks.
- A rollback drill restores the pre-exercise state with zero unexpected effects.

### Gate G5: Trusted promotion and operation

- Phase 3N emits fresh `trusted=true`, `l3_eligible=true`, and `production_ready=true` evidence for the exact commit.
- Migration, execution unlock, and Production release approvals are separately recorded.
- Promotion consumers independently verify workflow provenance and artifact attestation.
- Production health, alerting, rollback, and incident ownership are documented and exercised.
- The initial Production observation window completes without a release-blocking incident.

---

## 5. Roadmap to Goal

The order below is dependency-driven. An agent must not skip ahead by replacing missing external evidence with fixtures or repository assertions.

| Work package | Owner | Depends on | Deliverable | Exit condition | Progress impact |
|---|---|---|---|---|---:|
| WP0 Canonicalize current evidence | Sysop | none | Clean candidate commit and regenerated local/CI reports | All reports bind to the same current full SHA; no placeholder/stale report is treated as current | +2% |
| WP1 Define business acceptance contract | Jobs + Trainer + Ledger | none | **In progress:** versioned fail-closed contract, verifier, contract/abuse tests, CI and trusted Phase 3N/promotion wiring are implemented; non-AUC values remain deliberately unapproved | User approves thresholds; fresh current-commit out-of-time evidence passes the attested promotion gate | +5% |
| WP2 Finish operator workflow gaps | Harvester + Trainer + Oracle | WP1 for model decisions | **In progress:** quality bridge, authenticated Admin profiling viewer, and read-only feature provenance/INV-01 catalog are implemented and tested. Guarded redesign/retrain, standalone generation execution, advanced evaluation, and repair execution policy remain | Accepted workflows meet G1; intentionally script-only items have runbooks | +6% |
| WP3 Provision isolated Staging governance | Sysop | WP0 | **Partial:** three protected approval Environments, protected producer branch, and a distinct Vercel Staging deployment record exist. Current provider topology/commit, producer parity, evidence inputs, successful run selector, and authenticated Render/Supabase metadata remain absent | Authenticated metadata proves isolation and required reviewers without exposing values | +4% |
| WP4 Apply and verify hosted bootstrap | Sysop | WP3, explicit migration approval | Phase 3M migrations and hosted schema/history evidence | Bootstrap gate passes against Staging; rollback plan is recorded | +4% |
| WP5 Run Staging security and model checks | Sysop + Trainer + Oracle | WP4 | Auth/RLS/IDOR evidence and current candidate model report | G2 security boundary and model thresholds pass on candidate data | +4% |
| WP6 Run bounded operational exercise | Harvester + Sysop | WP4 | Live validation, two-instance crash/recovery, fencing, integrity, and rollback observations | Every Phase 3N saga/staging boolean is supported by non-synthetic evidence | +5% |
| WP7 Produce trusted Phase 3N evidence | Sysop | WP5, WP6, three approvals | Attested Phase 3N artifact for exact candidate | Verifier derives `trusted=true`, `l3_eligible=true`, `production_ready=true` | +3% |
| WP8 Promote and observe Production | Sysop + Jobs + Ledger | WP7, release approval | Controlled release, monitoring evidence, rollback readiness, business observation report | G5 passes and agreed observation period completes | +4% |

The percentages above are prioritization estimates, not additive score increments. They intentionally overlap across pillars; completion must trigger a fresh pillar-by-pillar score instead of summing the values. They do not authorize release by themselves.

### Immediate next sequence

1. Push the committed readiness candidate and regenerate exact-SHA CI evidence; archive or clearly label stale reports.
2. Obtain user approval for the missing business thresholds in WP1 and record a durable approval reference.
3. Generate current-commit out-of-time model evidence that satisfies the approved contract.
4. Execute WP3 and WP4 only with explicit external-environment and migration approval.
5. Execute the non-synthetic Staging exercises and rollback drill.
6. Run the trusted Phase 3N workflow; it now requires and attests the model acceptance report alongside operational evidence.
7. Promote only when both trusted Staging and model gates derive READY; then complete the Production observation period.

---

## 6. AI Agent Handoff Contract

Every AI agent starting work must report the following before changing code:

```yaml
work_package: WP0-WP8
goal: one sentence
current_commit: full 40-character SHA
evidence_used:
  - current executable evidence only
invariants_at_risk:
  - INV-xx or none
external_side_effects:
  - none, or exact approved effect
cheap_disconfirming_check: command or test
```

Every agent finishing work must report:

```yaml
files_changed:
tests_run:
evidence_created:
evidence_commit_sha:
exit_condition_met: true|false
remaining_blockers:
production_ready: false unless trusted Phase 3N says true
```

Rules:

- Use the owner skill named in the roadmap; use Jobs for cross-domain coordination.
- Read `docs/specs/SYSTEM.md` before edits.
- Never increase a progress score from implementation alone when the exit condition requires external evidence.
- Never label fixtures, disposable databases, local runs, or synthetic runs as Staging evidence.
- Never apply a migration, deploy, unlock execution, or perform a write-sensitive exercise without the corresponding explicit approval.
- Update this document only when an exit condition changes, and link the exact evidence used.

---

## 7. Status Update Procedure

For each status review:

1. Record the exact branch, full commit SHA, date, and worktree state.
2. Re-evaluate evidence freshness using Section 2.
3. Score each pillar independently; do not backsolve a desired overall percentage.
4. Update completed exit conditions and blockers.
5. Keep `Production NOT_READY` unless the trusted Phase 3N verifier derives READY for the exact candidate.
6. Record changes in a short dated entry below.

### Status history

| Date | Commit | Overall | Production | Change |
|---|---|---:|---|---|
| 2026-07-12 | `80556e8` | 65-70% | NOT_READY | Phase 3 baseline established |
| 2026-08-02 | `10267de` | 66% | NOT_READY | Phase 3M/N contracts and frontend checks pass; local full-stack assets, trusted Staging evidence, business acceptance contract, and current dependency remediation remain incomplete |
| 2026-08-02 | `d9bbcbc` + readiness working tree | 66% authoritative / 68% provisional | NOT_READY | Python 3.11 venv, fail-closed local config, empty DB fixture, two-service health smoke, 5-case Playwright smoke, full Python suite, and zero Critical/High dependency audits pass; commit-bound CI and external Staging evidence remain pending |
| 2026-08-02 | `6300e27` + WP1 working tree | 66% authoritative / 68% provisional | NOT_READY | The business gate is now versioned, fail-closed, tested, and wired into trusted Phase 3N/promotion. Threshold approval and real out-of-time evidence remain open, so no completion score is claimed. |
| 2026-08-02 | `c9b7c02` + profiling-viewer working tree | 66% authoritative / 68% provisional | NOT_READY | Exact-SHA local gates pass (Python 920, Frontend 242 including WP2, typecheck/build, Critical/High 0). WP2 fixes the Bearer-less profiling link with an Admin-only authenticated sandbox viewer; external CI and Staging remain pending. |
| 2026-08-02 | `c568e70` + feature-catalog working tree | 66% authoritative / 68% provisional | NOT_READY | WP2 exposes the existing feature catalog in `/feature-lab`, including the INV-01 future-field blocklist and engineered-feature provenance. It remains read-only and does not claim standalone generation completion. |
| 2026-08-02 | `3b9836b` + external read-only audit | 66% authoritative / 68% provisional | NOT_READY | Approval Environments and a distinct but stale Vercel Staging deployment record exist. The trusted producer is stale, the successful run selector and protected inputs are absent, the prior trusted run failed at execution approval, and Render/Supabase isolation is unverified. No external state was changed. |
| 2026-08-02 | `codex/fullstack-readiness` local candidate | 66% authoritative / 68% provisional | NOT_READY | WP1 now rebuilds acceptance evidence from strict out-of-time rows, checks temporal separation and canonical future fields, recomputes all metrics, and binds model/source digests. Python 949, feature consistency 73, model gate 72, Frontend 245, typecheck/build, and scanners pass locally; real observations, threshold approval, push/CI, and trusted Staging remain open. |

---

## 8. Source Documents

- `docs/specs/SYSTEM.md`: architecture, invariants, and AUC target.
- `docs/ui_workflow_completion_matrix.md`: 13 workflow completion inventory.
- `docs/frontend_execution_completion_roadmap.md`: frontend execution destination and phased gaps.
- `docs/phase3_staging_e2e_baseline.md`: L0-L4 baseline and Staging exit criteria.
- `docs/phase3h_production_readiness_gate.md`: fail-closed Production decision.
- `docs/phase3j_durable_saga_outbox_disposable_gate.md`: disposable saga evidence boundary.
- `docs/phase3l_staging_readiness_gate.md`: external Staging prerequisites.
- `docs/phase3n_staging_evidence.md`: trusted evidence and approval contract.
- `docs/model_acceptance_contract.md`: versioned business thresholds, evidence schema, and approval boundary.

Historical documents may contain stale versions or assumptions. When they conflict, prefer `docs/specs/SYSTEM.md`, executable current-commit evidence, and this status document.
