# Project Status and Roadmap

> Status date: 2026-08-02
> Evidence cutoff: repository artifacts through 2026-07-20 plus local validation on 2026-08-02
> Current branch at assessment: `codex/phase3n-staging-rollout`
> Current implementation checkpoint: `f2614e4` (`origin/develop`, merged by PR #26)
> Status: **overall 72.65%, reported as 73% (reasonable range: 71-75%), Production NOT_READY**
> Candidate deployment: exact SHA `f2614e457a371f6c14797a94f889376f968db76a`; CI run `30747412029` passed 12/12 jobs

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
| Git source | Readiness and append-only-upgrade work are merged into `origin/develop` at `f2614e4`; this handoff update is isolated on `codex/phase3n-staging-rollout` | The deployed candidate and trusted producer bind to the same merged commit |
| Node runtime | Node 24.12.0, npm 11.6.2; clean `npm ci` and the CI `--omit=optional` dependency-tree check passed. The current development tree contains 4 optional WASM packages reported as extraneous | Verified for reproducible install; current `node_modules` is not an exact clean-tree snapshot |
| Frontend tests | 32 test files and 335 tests passed on exact commit `a8e2f18` | Verified; existing React `act(...)` warnings remain non-blocking |
| Production build | Next.js 16.2.12 build completed and generated 71 routes | Verified; broad NFT trace and dependency-origin `url.parse()` warnings remain |
| Python runtime | Worktree-local Python 3.11.9 venv exists with CI requirements, pytest, FastAPI, LightGBM, pandas and scikit-learn | Verified |
| Python tests | The full suite passed 1,169 tests at readiness checkpoint `31c6b64`; the merged exact SHA `f2614e4` subsequently passed the complete Python CI job and every other required CI job | Confirmed by exact-SHA remote CI run `30747412029` |
| Local configuration | Ignored `.env` and `.env.local` contain local dummy endpoints and fail-closed write/scheduler/Saga switches; no secrets were copied | Ready for local health/fixture smoke, not authenticated hosted flows |
| Local operational data | A new empty 36 KiB `keiba/data/keiba_ultimate.db` fixture was initialized through the repository storage code | Ready for schema/startup smoke; real scrape/train/predict data remains absent |
| Services and E2E | FastAPI `/health` and Next.js `/api/health` returned 200; FastAPI OpenAPI and Next home returned 200; both unauthenticated protected-API probes returned 401; public fixture Playwright smoke passed 5/5; services were stopped afterward | Local integration slice verified |
| Dependency security | `@google-cloud/vision` 5.3.7 removes the vulnerable Google Vision/uuid path; `tsx` 4.23.1 and Vitest 4.1.10 move esbuild to 0.28.1. Full and production audits now contain 0 findings at every severity, and the mandatory dependency-tree install/check passes | Cleared locally and by exact-SHA remote CI |
| Model acceptance path | Strict row observations are recomputed into digest-bound evidence; the authorized owner approved every canonical threshold in GitHub issue #25; a one-shot Staging/Sandbox evaluator accepts only a fresh approved-contract report and registers it through the existing CAS RPC | Parent DB lacks eight required source capabilities; no real accepted evaluation or trusted Phase 3N run exists, and database evaluation remains structurally non-promoting |

Practical readiness:

- source review and frontend/contract-test work: approximately **90% ready**;
- self-contained local smoke execution: approximately **70% ready**;
- isolated Staging/Production operation: governed by the separate 55% operational-proof score below.

The parent worktree assets were not copied. This worktree now has independently generated local-only configuration, a Python 3.11 venv, and an empty schema fixture. Secrets and production data remain absent by design.

Exact-SHA CI run `30747412029` passed all 12 jobs for merged commit `f2614e457a371f6c14797a94f889376f968db76a`, including dependency security, Python, Frontend, Playwright, scanners, Phase 3G-J runtime gates, the two-database Phase 3M bootstrap replay, and both container builds. Hosted WP3/WP4 and Auth/RLS/IDOR proof raise only the operational-proof pillar; the authoritative overall value is now 72.65% (reported as 73%). This is not a release authorization.

### 0.2 External Staging and governance snapshot

The owner explicitly authorized isolated Staging changes and the fresh 19-migration application. The following provider state was then applied and measured on 2026-08-02:

| Boundary | Observed state | Assessment |
|---|---|---|
| Protected approval Environments | `staging-migration`, `staging-execution-unlock`, and `production-release` exist with required reviewer rules and explicit branch policies | Governance skeleton exists |
| Trusted producer | `security/phase3n-trusted-producer-v3` points to exact SHA `f2614e4`; repository variable `PHASE3N_TRUSTED_PRODUCER_SHA` has the same full SHA | Branch protection now enforces all 12 CI contexts, PR-only updates, resolved conversations, admin enforcement, and no force-push/deletion |
| Trusted evidence run | Run `29730598574` passed context and migration approval, then failed after waiting at the Staging execution boundary; no successful Phase 3N run exists | No trusted evidence artifact |
| Promotion selector | `PHASE3N_STAGING_EVIDENCE_RUN_ID` is absent | Promotion cannot select an approved run |
| Protected evidence inputs | No Environment secret names were present for the three Phase 3N Environments; the current workflow requires `PHASE3N_STAGING_OBSERVATION_B64` and `MODEL_EVALUATION_OBSERVATIONS_GZIP_B64` at execution unlock | Trusted workflow must fail closed |
| Vercel Staging | Team `team_JH2S20AGrjXdHHTsFTy3eUd5`, project `prj_PRd5AxU90qDwj67idD8MlB8XE6Zs`, deployment `8MycthGwkUP1tVCAMN7gjxkHqdud` | Production deployment of the distinct `keiba-ai-pro-staging` project is Ready at exact SHA `f2614e4` |
| Render Staging | Service `srv-d9nj2e8ae00c739sau0g`, deployment `dep-d9nkaj5aeets73c4l0h0` | Exact SHA `f2614e4` is live; `/health` returned 200 and the unauthenticated protected scrape-health probe returned 401 |
| Supabase Staging | New isolated project ref `btegligclxkwzefikbzm`, Tokyo region, created after deleting the authorized legacy Staging project | Fresh bootstrap history contains exactly 19 rows, ordinals 1-19, chain `f2ab3036...656908`, manifest `080bfd88...d7883`, and applied commit `f2614e4`; hosted catalog fingerprint capture was `7c80c248...f5618` |
| Auth/RLS/IDOR smoke | Three isolated, auto-confirmed Free/Premium/Admin users were created temporarily and exercised with real JWTs through the hosted Data API | All 11 required checks passed: three logins, anonymous denial, Free/Premium isolation, Admin own access, foreign-profile denial, role-escalation denial, privileged-RPC denial, and private-bucket write denial; cleanup left 0 test users and 0 profiles |
| Secret hygiene | Render uses a purpose-specific rotated modern Supabase secret; the first temporary secret was deleted and legacy JWT API keys were disabled after migration work | No provider credential is stored in repository evidence or this document |
| Preview cost control | The initially created Preview Branch inherited legacy schema and safely failed the fresh-target preflight; it was deleted immediately | No Preview Branch compute remains active |

This completes WP3 and WP4 and the Auth/RLS/IDOR slice of WP5 for the exact candidate. It does not complete WP5-WP7: the protected observation secrets are intentionally still absent because the required strict out-of-time model rows, multi-instance crash/recovery, integrity, and rollback observations do not yet exist.

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

### 1.3 Approved business decisions

The authorized owner approved the following initial Phase 3N contract in GitHub issue #25 on 2026-08-02:

- AUC >= 0.85, Brier score <= 0.20, and expected calibration error <= 0.05;
- out-of-time ROI >= 3.0%, maximum drawdown <= 20.0%, and ROI delta to baseline >= 1.0 percentage point;
- at least 100 qualifying bets and 1,000 evaluation samples over at least 90 days;
- P95 prediction latency <= 500 ms and data freshness <= 30 minutes;
- out-of-time holdout only, no future-field leakage, and documented candidate/baseline staking and cost treatment.

These values are encoded in `config/model_acceptance_contract.v1.json` with the durable approval comment as the reference. Business-goal completion still requires fresh current-commit observations to pass every approved threshold.

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
| Repository safety and quality gates | 25% | 88% | 22.0% | Auth/fail-closed gates, zero Critical/High dependency enforcement, Python/Frontend/Playwright, Phase 3M replay, container builds, and security scanners pass on exact-SHA CI run `30747412029` |
| Staging and Production operational proof | 20% | 55% | 11.0% | Exact-commit provider identities, hosted fresh bootstrap, protected GitHub boundaries/producer, and all 11 hosted Auth/RLS/IDOR checks are proven; multi-instance/integrity/rollback exercises, accepted model evidence, and the trusted artifact remain open |
| **Overall** | **100%** |  | **72.65% authoritative** | Report as 73%; Production remains NOT_READY until the remaining non-synthetic Staging, trusted Phase 3N, and business gates pass |

Workflow scoring assigns 1.0 point to `complete`, 0.6 to `partial`, and 0 to `missing`. Thus $(6 + 7 \times 0.6) / 13 = 78.5\%$, conservatively reported as 78%. Other pillar scores are evidence-based assessments and must be revisited when their exit conditions change.

### 3.1 Interpretation

- **Product implementation:** approximately 78%.
- **Repository-level safety and quality:** approximately 88%.
- **Real-environment readiness:** approximately 55%.
- **Overall goal:** **72.65%, reported as 73%**, with a reasonable uncertainty range of **71-75%**.

The overall score is now above the 2026-07-12 baseline because isolated provider topology and the hosted bootstrap are proven. Later phases substantially improved safety contracts, but they have not yet closed the non-synthetic Staging evidence and business-validation gaps.

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
- Historical model quality is encouraging and the business acceptance criteria are approved, but fresh current-commit evidence is incomplete.

### 3.4 What blocks Production

1. Non-synthetic multi-instance crash/recovery and stale-fence rejection are not proven.
2. Database/cache integrity and rollback drill evidence are not proven.
3. The three GitHub Environment approval boundaries are configured but have not been exercised by a successful current-candidate run.
4. A fresh trusted Phase 3N artifact for the exact candidate commit does not exist.
5. Business success thresholds are approved and encoded, but fresh current-commit row
   observations have not yet passed them. Aggregate metrics are recomputed by trusted
   code and bound to model/source digests; approval alone cannot produce acceptance.
6. The read-only parent-DB audit reports zero prediction/OOT/settled-label rows and all eight strict source capabilities absent, so `MODEL_EVALUATION_OBSERVATIONS_GZIP_B64` cannot be honestly produced from current data.

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
| WP0 Canonicalize current evidence | Sysop | none | **Complete for the implementation candidate:** merged commit `f2614e4`, readiness Python 1,169/Phase 3M 17-of-17 checks, and exact-SHA CI run `30747412029` with 12/12 jobs green | Current implementation, deployments, trusted producer, and CI artifacts bind to the same full SHA; historical reports remain explicitly labeled non-current | +2% |
| WP1 Define business acceptance contract | Jobs + Trainer + Ledger | none | **Contract approved:** the authorized owner approved every threshold and the out-of-time/no-leakage/staking policy in GitHub issue #25. The versioned fail-closed contract, verifier, abuse tests, CI/trusted Phase 3N wiring, and sanitized source audit are implemented. The parent DB cannot supply the required strict observations | Fresh current-commit out-of-time evidence passes the approved attested promotion gate | +5% |
| WP2 Finish operator workflow gaps | Harvester + Trainer + Oracle | WP1 for model decisions | **In progress:** quality bridge, authenticated Admin profiling viewer, read-only feature provenance/INV-01 catalog, strict retrain payload hashing, an Admin-only eligibility assessment, deployed-environment blocks on legacy model training/activation/deletion/repair, an Admin request/independent-decision/job-status panel, private two-person approval/job ledgers, service-only CAS/lease/fencing transitions, immutable private-bucket artifact/evaluation registration, a service-only execution bundle, an isolated OOT trainer, and a fail-closed one-shot coordinator are implemented. A bounded one-shot dispatcher selects at most five exact candidates and delegates to the fenced coordinator; a one-shot evaluator rebuilds evidence from strict rows in memory, requires the canonical approved contract and accepted verifier report, and records it through CAS while keeping promotion false. A separate reconciler handles expired leases and old unregistered exact-name objects with an immutable outcome ledger. Deployment and recurring scheduling, hosted PostgreSQL/Storage runtime evidence, trusted Phase 3N attestation and candidate comparison, separate switch/retirement approvals, standalone generation execution, and advanced evaluation remain | Accepted workflows meet G1; intentionally script-only items have runbooks | +6% |
| WP3 Provision isolated Staging governance | Sysop | WP0 | **Complete for candidate `f2614e4`:** authenticated Vercel/Render/Supabase identities are recorded, the three approval Environments restrict the trusted branch, v3 producer parity and variable binding are exact, and v3 is protected against direct/force/deletion changes | Authenticated metadata proves isolation and required reviewers without exposing values | +4% |
| WP4 Apply and verify hosted bootstrap | Sysop | WP3, explicit migration approval | **Complete for candidate `f2614e4`:** a new isolated Supabase Staging project received the exact fresh 19-migration bundle; history count/order/chain/manifest/commit and hosted catalog fingerprint were measured. The rejected inherited-schema Preview Branch and legacy Staging project were deleted under explicit authorization | Bootstrap gate passes against Staging; rollback plan is recorded | +4% |
| WP5 Run Staging security and model checks | Sysop + Trainer + Oracle | WP4 | **Partial:** all 11 hosted Auth/RLS/IDOR checks passed with real Free/Premium/Admin JWTs and complete test-user cleanup. The parent source audit still reports zero strict OOT observations and eight missing source capabilities, so the current candidate model report cannot be produced | G2 security boundary and model thresholds pass on candidate data | +4% |
| WP6 Run bounded operational exercise | Harvester + Sysop | WP4 | Live validation, two-instance crash/recovery, fencing, integrity, and rollback observations | Every Phase 3N saga/staging boolean is supported by non-synthetic evidence | +5% |
| WP7 Produce trusted Phase 3N evidence | Sysop | WP5, WP6, three approvals | Attested Phase 3N artifact for exact candidate | Verifier derives `trusted=true`, `l3_eligible=true`, `production_ready=true` | +3% |
| WP8 Promote and observe Production | Sysop + Jobs + Ledger | WP7, release approval | Controlled release, monitoring evidence, rollback readiness, business observation report | G5 passes and agreed observation period completes | +4% |

The percentages above are prioritization estimates, not additive score increments. They intentionally overlap across pillars; completion must trigger a fresh pillar-by-pillar score instead of summing the values. They do not authorize release by themselves.

### Immediate next sequence

1. Generate current-commit out-of-time model evidence that satisfies the approved contract.
2. Execute WP3 and WP4 under the granted external-environment and migration approval.
3. Execute the non-synthetic Staging exercises and rollback drill.
4. Run the trusted Phase 3N workflow; it requires and attests the model acceptance report alongside operational evidence.
5. Promote only when both trusted Staging and model gates derive READY; then complete the Production observation period.

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
| 2026-08-02 | `codex/fullstack-readiness` guarded-retrain candidate | 66% authoritative / 68% provisional | NOT_READY | WP2 now produces canonical actor/model/feature/data/code-bound retrain payloads and exposes an Admin-only, non-executing approval eligibility assessment. Frontend 274, typecheck, targeted lint, 69-route build, and scanners pass; durable approval, job/artifact runtime, evaluation, and activation remain disabled. |
| 2026-08-02 | `codex/fullstack-readiness` activation-bypass guard | 66% authoritative / 68% provisional | NOT_READY | The legacy direct active-model pointer mutation is blocked in deployed/unknown environments at both Next and FastAPI layers and removed as an enabled UI action. Python 957 and Frontend 281 pass; only explicit local/test compatibility remains. |
| 2026-08-02 | `codex/fullstack-readiness` repair-policy guard | 66% authoritative / 68% provisional | NOT_READY | Legacy repair and incomplete-race rescrape execution are now blocked before proxying in deployed or unknown environments and require explicit local/test opt-in at both Next and FastAPI boundaries. Frontend 295, the 115-test operational safety slice, typecheck, targeted lint, and scanners pass; durable approval-bound execution and trusted Staging evidence remain open. |
| 2026-08-02 | `codex/fullstack-readiness` authz-canonical guard | 66% authoritative / 68% provisional | NOT_READY | The runtime extractor now recognizes the shared custom Premium/Admin guard used by the model-redesign summary and Notion report routes. The 74-route generated authorization matrix matches runtime and canonical policy; 40 Phase 2 tests and both safety scanners pass. |
| 2026-08-02 | `codex/fullstack-readiness` durable-approval candidate | 66% authoritative / 68% provisional | NOT_READY | WP2 now contains an Admin-only, actor/hash-bound, expiring, CAS-versioned and two-person model-retrain approval ledger. RLS, append-only audit, immutable bindings, and structural `execution_enabled=false` / `job_created=false` guards are wired into the 12-migration canonical Phase 3M bootstrap. Frontend 306, Python 965, 77-route authz, 70-route build, SQL parse, and scanners pass locally; Docker runtime and hosted Staging application remain unproven. |
| 2026-08-02 | `codex/fullstack-readiness` direct-training bypass guard | 66% authoritative / 68% provisional | NOT_READY | Legacy synchronous/asynchronous model artifact writers now fail closed in deployed and unknown environments at Next and FastAPI boundaries, reject before job creation, and are disabled in the normal UI. Frontend 314, Python 973, 77-route authz, 70-route build, lint/typecheck, scanners, and Critical/High 0 audit pass; the approval-bound durable job/artifact runner remains unimplemented. |
| 2026-08-02 | `codex/fullstack-readiness` model-retirement bypass guard | 66% authoritative / 68% provisional | NOT_READY | Direct local/Supabase model deletion now fails closed before mutation in deployed and unknown environments at Next and FastAPI boundaries. Frontend 322, Python 980, 77-route authz, 70-route build, lint/typecheck, and both scanners pass; explicit local/test compatibility remains while a separate durable retirement approval is still unimplemented. |
| 2026-08-02 | `03e4963` approval-bound job candidate | 66% authoritative / 68% provisional | NOT_READY | WP2 now atomically queues one actor/hash/CAS-bound durable job per independently approved retrain request, records append-only approval/job events, and keeps execution/artifact fields structurally false. The canonical bootstrap has 13 migrations; exact-commit Python 984, Frontend 329, 79-route authz, 71-route build, SQL parse, lint/typecheck, and scanners pass. Worker lease/claim, isolated artifact execution, and hosted application remain pending. |
| 2026-08-02 | `505c65b` fenced-worker candidate | 66% authoritative / 68% provisional | NOT_READY | WP2 adds service-only CAS claim/heartbeat/start/failure/recovery, bounded leases, monotonic fencing tokens, approval rechecks, immutable bindings, append-only events, and deletion guards while artifact writes remain structurally impossible. The canonical bootstrap has 14 migrations; exact-commit Python 989, Frontend 330, FastAPI 63/Next 79 authz, 71-page build, SQL parse, lint/typecheck, scanners, and Critical/High 0 audit pass. A deployed dispatcher/trainer, isolated artifact registration/evaluation, remote CI, and hosted Staging proof remain pending. |
| 2026-08-02 | `0693ab7` fenced-artifact candidate | 66% authoritative / 68% provisional | NOT_READY | WP2 now lets only the live fenced worker bind one strictly named, digest-bound, size-bounded existing object from the private `models` bucket into an immutable registration ledger and terminal `artifact-registered` state. It does not attest contents, evaluate, activate, or clean orphan uploads. The canonical bootstrap has 15 migrations; exact-commit Python 994, Frontend 331, FastAPI 63/Next 79 authz, 71-page build, SQL parse, lint/typecheck, scanners, and Critical/High 0 audit pass. Deployed training/upload, trusted evaluation, remote CI, and hosted Staging proof remain pending. |
| 2026-08-02 | `7b75805` approval-workbench candidate | 66% authoritative / 68% provisional | NOT_READY | The Admin workbench now exposes exact-preview request creation, shared-ID loading, independent decision, requester-only approved job submission, and job-state refresh. It never dispatches a worker or writes an artifact; RPC guards retain two-person/CAS enforcement. Exact-commit Python 994, Frontend 334, 71-page build, lint/typecheck, and scanners pass. Hosted ledger application, deployed training/upload, evaluation, remote CI, and Staging proof remain pending. |
| 2026-08-02 | `059d9bb` accepted-evaluation candidate | 66% authoritative / 68% provisional | NOT_READY | WP2 now validates and immutably records only a fresh sanitized accepted-report bound to the registered artifact, exact approved dry-run commit, approved contract projection, and all verifier checks. JSON null bypasses fail closed. `trusted_promotion_evidence` and `promotion_eligible` remain structurally false, so this cannot activate or promote a model. The canonical bootstrap has 16 migrations; exact-commit Python 999, Frontend 335, FastAPI 63/Next 79 authz, 71-page build, SQL parse, lint/typecheck, and scanners pass. Deployed execution, real observations/threshold approval, remote CI, and hosted Staging proof remain pending. |
| 2026-08-02 | `0b7ec25` approved-execution boundary candidate | 66% authoritative / 68% provisional | NOT_READY | WP2 now has a 17th canonical migration that projects a service-only execution bundle only to the live lease/fence/version owner and rechecks commit, active model, snapshot, periods, safety checks, and the recomputed feature contract. Python independently validates the RPC projection and the trainer isolates snapshot/output paths, suppresses legacy sync/catalog/upload side effects, fits preprocessing only on the approved training period, and keeps validation evaluation-only. Exact-commit Python 1,029, Frontend 335, FastAPI 63/Next 79 authz, 71-page build, local two-service 200/401 smoke, lint 0 errors, scanners 0, and production audit Critical/High 0 pass. Real PostgreSQL bootstrap, deployed dispatcher/heartbeat/snapshot/uploader execution, real OOT observations and approved thresholds, push/remote CI, and trusted hosted Staging evidence remain pending. |
| 2026-08-02 | `d44a6e4` fenced-runner candidate | 66% authoritative / 68% provisional | NOT_READY | WP2 now includes a fail-closed Staging/Sandbox one-shot runner and exact Supabase RPC/Storage adapter. It binds job/version/worker/fence/commit/active-model/policy, keeps the lease alive across snapshot copy, training and upload, hashes and uploads from one file handle, prevents non-private/upsert writes, cooperatively cancels on lease loss, and removes an uploaded object when registration fails. Exact-commit Python 1,048 and both safety scanners pass with a clean worktree. No migration was applied and no RPC, artifact upload, hosted worker, real OOT evaluation, threshold approval, remote CI, or trusted Staging run was performed; abrupt-process orphan reconciliation and scheduling also remain pending. |
| 2026-08-02 | `a8e2f18` zero-audit candidate | 66% authoritative / 68% provisional | NOT_READY | The direct Google Vision dependency and development toolchain were compatibly updated, removing the five production Moderate uuid-path findings and the development esbuild Low finding. On the exact commit, Frontend 335, typecheck, 71-page build, lint with 0 errors, full/production audits with 0 findings, the CI-equivalent mandatory dependency tree, and both safety scanners pass locally. Remote exact-SHA CI and all hosted Staging/model evidence remain pending, so the score and release verdict do not advance. |
| 2026-08-02 | `767c514` orphan-reconciliation candidate | 66% authoritative / 68% provisional | NOT_READY | The 18th canonical migration and a fail-closed Staging/Sandbox one-shot reconciler now recover expired claimed/running leases and expose cleanup candidates only for old terminal `failed` jobs with no immutable artifact registration. Exact object names, job/digest/time tokens, private-bucket deletion results, failed deletions, and candidate-zero scans are recorded in an immutable service-only ledger. Exact-commit Python 1,083 and both safety scanners pass. The migration has not run on PostgreSQL or hosted Staging; no real object was deleted, and scheduler, trusted evaluation, remote CI, real OOT evidence, threshold approval, and Phase 3N evidence remain pending. |
| 2026-08-02 | `bbe9df3` bounded-dispatcher candidate | 66% authoritative / 68% provisional | NOT_READY | The 19th canonical migration adds a read-only service-only queue projection, and a fail-closed Staging/Sandbox one-shot dispatcher selects at most five exact policy/commit/active-model candidates, validates snapshot digests and all returned bindings again in Python, and invokes the existing fenced coordinator sequentially. Exact-commit Python 1,129 and both safety scanners pass. No migration, queue scan, claim, snapshot read, training, upload, or registration was performed against real PostgreSQL/Storage; deployment, recurring scheduling, remote CI, real OOT evidence, threshold approval, trusted evaluation, and Phase 3N evidence remain pending. |
| 2026-08-02 | `9665c8c` model-source audit candidate | 66% authoritative / 68% provisional | NOT_READY | A sanitized read-only/immutable SQLite audit now checks the selected model/cutoff, same-day post-cutoff predictions, settled label/class coverage, timezone-aware prediction timestamps, and complete freshness/settlement/candidate-wager/baseline-wager/latency fields without exporting paths or rows. Exact-commit Python 1,137 and both safety scanners pass. The parent DB has 1,014 same-day post-cutoff active-model rows but only 15 settled labels and lacks all eight strict source capabilities, so it cannot be promoted into trusted OOT evidence by copying or aggregate reconstruction. |
| 2026-08-02 | `7764fa7` accepted-evaluator candidate | 66% authoritative / 68% provisional | NOT_READY | A fail-closed Staging/Sandbox one-shot evaluator now loads strict OOT rows only from an absolute non-symlink input, recomputes evidence in memory, requires the canonical contract to be approved and every verifier threshold/check to pass, independently revalidates the sanitized report, and registers it with exact job/CAS/evaluator/commit bindings. Exact-commit Python 1,168 and both scanners pass. The repository contract is still draft, no real row set or RPC execution exists, and the resulting DB state remains `promotion_eligible=false`; only trusted Phase 3N attestation can authorize promotion. |
| 2026-08-02 | `31c6b64` exact-SHA CI candidate | 67.65% authoritative / 68% reported | NOT_READY | The PostgreSQL 17.6 approval-ledger parse failure is fixed and regression-guarded. Local Python 1,169 and the Phase 3M two-fresh-database 17-of-17 gate pass; remote CI run `30741847311` is green across all 12 jobs, including Playwright, dependency/security gates, Phase 3G-J, Phase 3M bootstrap replay, and both container builds. Isolated hosted Staging, approved business thresholds, real OOT observations, trusted Phase 3N evidence, and release approval remain open. |
| 2026-08-02 | `codex/fullstack-readiness` approved-threshold candidate | 67.65% authoritative / 68% reported | NOT_READY | Repository owner `yuki20001105` approved the complete initial Phase 3N model acceptance contract in GitHub issue #25. The durable comment timestamp and URL, all eleven thresholds, and out-of-time/no-leakage policy are encoded in the canonical contract. The score remains unchanged until fresh current-commit OOT observations pass; isolated hosted Staging and trusted Phase 3N evidence remain open. |
| 2026-08-02 | `codex/phase3m-append-only-upgrade` candidate | 67.65% authoritative / 68% reported | NOT_READY | Authenticated provider audit found the isolated Supabase Staging project at ref `xitrnivjskfepateedms` with the unchanged 11-migration `861f46c...` Phase 3M prefix, while the candidate contains 19 migrations. A commit/segment-bound renderer now preserves every old history row and appends only a byte-identical manifest suffix; fresh Phase 3N proof remains assigned to a short-lived isolated Preview Branch. No hosted upgrade or Preview migration is claimed by repository implementation alone. |
| 2026-08-02 | `f2614e4` isolated Staging rollout | 71.65% authoritative / 72% reported | NOT_READY | PR #26 is merged and exact-SHA CI run `30747412029` passes 12/12. Distinct Vercel and Render Staging deployments are live at the exact commit; a new isolated Supabase Staging project has exactly 19 fresh bootstrap history rows bound to the canonical chain, manifest, and commit. The v3 trusted producer is exact-SHA-bound and branch-protected. The temporary inherited-schema Preview Branch and legacy Staging project were deleted. Production remains blocked by real Auth/RLS/IDOR, multi-instance crash/recovery, integrity/rollback, strict OOT row observations, protected observation secrets, and a successful trusted run. |
| 2026-08-02 | `f2614e4` hosted Auth/RLS/IDOR smoke | 72.65% authoritative / 73% reported | NOT_READY | Temporary Free, Premium, and Admin users logged in successfully against isolated Supabase Staging. Anonymous profile access was denied; each user could read only its own profile; cross-user reads returned zero rows; role escalation, privileged browser RPC, and private-bucket writes were denied. Cleanup verified 0 remaining test users and 0 profiles. Model OOT rows, multi-instance recovery, integrity/rollback, protected inputs, and trusted evidence remain open. |

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
- `docs/repair_execution_policy.md`: fail-closed direct-repair boundary and prerequisites for future approval-bound execution.

Historical documents may contain stale versions or assumptions. When they conflict, prefer `docs/specs/SYSTEM.md`, executable current-commit evidence, and this status document.
