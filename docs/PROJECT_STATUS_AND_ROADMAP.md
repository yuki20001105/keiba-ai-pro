# Project Status and Roadmap

> Status date: 2026-08-22
> Evidence cutoff: hosted Staging, trusted evidence, Production credential rotation, and Production schema migration through 2026-08-22
> Current branch at assessment: `codex/phase3n-exact-staging-evidence`, based on merged PR #28
> Current runtime implementation checkpoint: `86a2d314a641160e852d3597396aadcd03e81347`, which is CI-green, Live on Render Staging, and verified against the same effective Phase 3N candidate SHA
> Status: **overall 77.15%, reported as 77% (reasonable range: 75-80%), Production NOT_READY**
> Limited Production observation readiness: **84.53%** when the model-business-value pillar is reported separately; the trusted system gate and Production database adoption pass, but the exact candidate has not been deployed to Production
> Candidate status: the merged exact-SHA `middle` regression succeeded for race `202604020812`: 15 horse predictions, including one qualifying virtual bet, are append-only in Supabase under `86a2d314...`; all 15 carry real non-final odds and the nonempty cache delete/rebuild exercise passes over the complete 60-row ledger. Earlier batches bound to `2d39111...` (30 rows) and `961a5be...` (15 rows) remain explicit audit history and are excluded from exact-candidate counts. Results are not settled, so formal progress remains 0/1,000 valid samples, 0/100 settled qualifying bets, and 0/90 elapsed settled days. The first cold-start/on-demand batch records P95 latency 46.343 seconds, so it is not evidence for the <=500 ms model-validation gate. The market-free OOF winner-meta AUC remains 0.7581 against the approved 0.85 gate; all generated replacement candidates remain research-only, unapproved, and undeployed

This is the canonical handoff document for answering three questions:

1. What is the project trying to achieve?
2. How much of that goal is demonstrably complete?
3. What must happen, in what order, before the goal is complete?

Do not infer readiness from code presence, a green synthetic test, or an old report. Use the evidence rules in this document.

## Current Phase 3N checkpoint (2026-08-22)

This section supersedes older point-in-time values below. Historical entries remain for auditability.

| Boundary | Current evidence | Remaining exit condition |
|---|---|---|
| Release contract split | `limited-production-observation-v1` separates `system_release_ready` from `model_business_validated`. The existing full READY path remains intact. Limited mode requires exact trusted system evidence and forces `MODEL_RUNTIME_STATUS=observation`, automatic betting off, deployed activation/training off, prospective `middle` observations, and rollback readiness. PR #28 is merged, trusted producer v4 is locked, and protected run `31928529807` derives trusted system READY for exact SHA `86a2d314...` | Finish monitoring/alert ownership and execute the separately controlled observation-only Production release; business validation remains deferred |
| Isolated Staging | Vercel, Render, and Supabase are isolated. Supabase preserves the original 19 rows and approved append-only ordinals 20-21; Render remains one Free instance and is Live at `86a2d314a641160e852d3597396aadcd03e81347`. The effective `PHASE3N_CANDIDATE_COMMIT_SHA` is also `86a2d314...`, was revealed and rechecked after save, and the same commit was redeployed | Preserve the deployed runtime identity and append-only history through trusted evidence collection |
| Multi-instance HA/fencing | A fresh 21-migration Supabase Preview Branch and two Render Starter instances exercised the real `cache-rebuild` operation at exact candidate `86a2d314...`. Instance `fgmqt` was SIGKILLed while holding fence token 2; instance `skpvn` reclaimed token 3, rebuilt and verified the cache from four real Staging HA rows, applied one immutable effect, and rejected the stale token-2 apply. Render was then restored to its byte-identical pre-test environment, one Free instance, and healthy observation-only runtime; the Preview Branch was deleted. The sanitized report is `reports/phase3n_non_synthetic_ha_rollback_20260816.json` | Bind this non-synthetic exercise into the complete protected system-observation envelope and have the trusted producer independently verify it |
| Observation collection | Race `202604020812` supplied a complete result-unknown `middle` snapshot with 15 real odds. Authenticated analyze returned 200 after the exact environment binding was corrected, and Supabase contains 15 horse predictions joined to merged exact SHA `86a2d314...`, including one qualifying virtual bet. The complete ledger has 60 rows: 15 current and 45 retained audit rows under the two prior SHAs. No synthetic, forecast, final-result, retrospective, or Production row was used. A later pre-result wake/analysis check remained complete (15 predictions, zero missing odds, 1,800 m): cold `/health` was 31.868 s, the first full analysis was 54.436 s, and six successful warm-cache calls measured P50 413 ms / P95 974 ms before the E2E account reached HTTP 429. The sanitized measurement is retained in `reports/phase3n_warm_latency_86a2d314.json` | Reconcile the race only after the authoritative result, then continue prospective collection to 90 settled days, 1,000 valid samples, and 100 settled qualifying bets. The six cache-hit samples are insufficient and do not isolate model inference, so the 500 ms prediction gate remains open |
| Historical OOT model evaluation | A read-only point-in-time replay against local real history after the model's 2026-02-01 cutoff used 980 complete races and 10,768 settled entries over 160 days. Brier 0.0791, ECE 0.0265, maximum drawdown 3.60%, 980 qualifying bets, and +12.71 percentage-point ROI delta to baseline pass; AUC 0.7158 and candidate ROI -8.27% fail | Treat this as fast provisional model-selection evidence, not prospective Staging evidence. Retrain/replace the candidate and rerun a reproducible OOT evaluation before spending 90 days collecting a model that already misses AUC/ROI |
| Speed-deviation annual walk-forward | Strict outer years 2020-2024 remain unseen by iteration and meta-model fitting. The ability matrix now excludes the full direct and derived market family, including implied probability, race odds z-score, entropy, top-3 market mass, normalized popularity, and payout history. Strictly shifted prior-speed histories feed the speed-deviation regressor, and only inner-period OOF speed scores plus pre-race fields train the winner meta-model. Across 229,190 entries and 16,644 races it records RMSE 2.4954, Spearman 0.6496, winner AUC 0.7581, Brier 0.06260, and ECE 0.00301; 2024 AUC reaches 0.7720. All future/market intersections are empty. AUC still fails 0.85 and ROI/DD/bet gates remain closed because genuine point-in-time historical odds do not exist | Keep the candidate unapproved and undeployed. Preserve this as the market-free ability baseline; next improvement requires richer legitimate pre-race context or a newly reserved period. Do not tune further on the now-observed 2020-2024 outer years, and keep nested purchase search blocked until timestamped odds exist |
| Official history and OOF value path | The free/no-contract outcome route is complete. The official-host-only index contains 1,729 unique JRA PDFs for 2019-2024; all exact bytes have URL, UTC retrieval time, size, and SHA-256. Conversion produced 273,187 timed runners in 19,846 flat races. It audited 755 jump races and explicitly excluded 87 unreadable/incomplete flat-race layouts plus three unsupported-font source documents rather than guessing. Full-bundle dry-run validated all 273,187 records without writing, then the identical manifest `4e52d5f...d399e` was appended to a recoverable research DB copy. `PRAGMA quick_check` is `ok`; annual authorized outcome coverage is 3,281-3,325 races and passes the speed-fold readiness gate. Result-time odds remain quarantined outside the training payload. The existing OOF evaluator now fails closed unless complete `middle`, pre-start, <=30-minute quotes carry source and one exact non-zero SHA; it enforces an Inner-only 5-15%/one-wager search, 95% ROI lower bound, drawdown, baseline delta, annual stability, neighborhood robustness, and digest-bound audit output | Preserve this immutable research evidence and its reviewed exclusions. Treat 2020-2024 as an observed fixed ability benchmark. Collect genuine point-in-time odds prospectively in Staging; until those rows exist, the hardened evaluator must produce no selected or accepted ROI policy |
| Staking/payout | Owner approval in issue #29 is durably bound to `phase3n-tansho-flat-v1`; exact-commit CI and Staging health/auth regression pass | Accumulate and settle real qualifying wagers under the approved policy |
| Cache integrity | The authorized non-paid local runtime read all 60 append-only prediction rows from Supabase PostgreSQL, wrote and deleted the cache, rebuilt it from the database, and verified identical DB/cache SHA-256 `653ca2f8...7cbc7`, zero missing/duplicate/stale cache rows, and `database_unchanged=true`. The evidence is bound to exact candidate `86a2d314...` and saved at `reports/phase3n_cache_integrity_evidence.json`; Render Shell remains intentionally unused because Free does not support it | Keep PostgreSQL authoritative. The protected observation schema also requires ordered before/after capture timestamps, which the current cache report does not contain; perform one authorized rerun with an external start/end timestamp wrapper before B64 registration |
| Repository gates | PR #28 merged as exact candidate `86a2d314...`; its candidate tree passed all 13 CI jobs and Vercel checks before merge. Render is Live at the same full SHA, authenticated health returned 200, unauthenticated protected access returned 401, authenticated analysis returned 200, and append-only observation insertion returned 15/0 inserted/duplicate | Preserve green exact-commit gates through trusted evidence and release workflow execution |
| Trusted evidence | Protected run `31928529807` passed all five jobs on immutable producer `c8308a3...`. The sanitized observation binds exact candidate `86a2d314...`, fresh 21-migration proof, hosted Auth/RLS/IDOR, timestamped 75-row cache integrity, exact Vercel/Render/Supabase identities, non-synthetic HA/fencing/rollback, all saga/staging checks, and three distinct Environment approvals. GitHub-signed evidence and gate artifacts independently verify; the gate derives `trusted=true`, `l3_eligible=true`, and `production_ready=true` with no failure codes. Repository selectors now pin this run and producer SHA | Preserve the immutable run and selectors through the controlled release. `MODEL_EVALUATION_OBSERVATIONS_GZIP_B64` remains deferred to full business validation after the observation period |
| Production legacy-schema adoption | Explicit owner approval `codex-user-instruction-2026-08-22-production-migration-approval` is durably bound to contract `27372764...89e8` and apply SQL `42249f1a...20a7`. After Production server-key rotation, legacy-key disablement, old-key HTTP 401 proof, Render maintenance suspension, physical-backup verification, and a read-only PASS preflight, the exact single transaction committed. Independent audit records 21 history rows bound only to `86a2d314...`, 24 preserved archive tables, 45 canonical public tables, zero public tables without RLS, zero direct anon table privileges, 19 policies, four Auth users, all 146 model Storage objects, and the Phase3N observation table. Render resumed with health 200; sanitized evidence is `reports/phase3n_production_credential_rotation_and_migration_20260822.json` | Keep the database canonical and legacy keys disabled. Production remains NOT_READY until safety variables are reviewed, exact candidate `86a2d314...` is deployed observation-only, monitoring/alert/rollback smoke passes, and the Limited Production release is recorded |

The operational-proof pillar advances from 85% to 90% because Production credential rotation and the reviewed 21-migration legacy-schema adoption now pass with independent postconditions. It does not advance further because the controlled exact-candidate Production deployment, Production smoke/monitoring evidence, and model-business observation window remain open. The ML/business-value pillar remains 55% because the current evidence still fails AUC and ROI acceptance. The weighted calculation is:

`78% * 30% + 55% * 25% + 88% * 25% + 90% * 20% = 77.15%`

The release plan now has two explicit finish lines. Limited system release: finish monitoring/alert/rollback and critical operator procedures -> review/merge the exact tree -> update and independently review the trusted producer -> produce trusted system evidence -> authorize and deploy only `MODEL_RUNTIME_STATUS=observation` with automatic betting disabled -> pass Production smoke and rollback. Model validation continues after that release: settle prospective `middle` observations -> freeze the Inner-selected purchase policy -> apply it once to the untouched Outer stream -> pass 90 days/1,000 settled samples/100 qualifying bets plus AUC/ROI/DD/calibration/latency/freshness -> produce trusted model evidence -> move to `validated`; `active` and any automatic betting require a later independent approval. Neither retrospective replay, synthetic data, forecast odds, final-result odds, nor fabricated wagers may be registered as prospective evidence.

---

## 0. Phase 3O Naming Clarification

`keiba-ai-pro-phase3o` is the name of this Git worktree directory. As of 2026-08-02, **Phase 3O is not a formally defined or implemented project phase**:

- no Phase 3O specification, workflow, test suite, branch, or commit exists;
- repository phase artifacts currently end at Phase 3N;
- `.git` identifies this directory as a worktree of the parent `keiba-ai-pro` repository;
- the former `fix/phase3n-bootstrap-equivalence` commit `10267de` is already an ancestor of `origin/develop` and was merged by `d9bbcbc`;
- current work now continues from exact `origin/develop` on `codex/fullstack-readiness`.

Therefore, the directory suffix must not be counted as completed Phase 3O work. If the next phase is named Phase 3O, it should first receive an approved scope and exit contract. A reasonable proposed scope is **current-commit evidence reconciliation plus isolated Staging execution**, corresponding to WP0 and WP3 through WP7 below. This proposal is not yet an approved phase definition.

### 0.1 Historical local worktree environment snapshot (2026-08-02)

This worktree is suitable for source inspection, frontend development, and focused contract tests. It is not yet a self-contained full-stack runtime.

| Area | Observed state on 2026-08-02 | Assessment |
|---|---|---|
| Git source | Candidate branch `codex/fullstack-readiness` remains based on `origin/develop` commit `d9bbcbc` and is 44 commits ahead at checkpoint `31c6b64`; the branch is pushed to origin | Correct base and remotely available; no PR or merge is implied |
| Node runtime | Node 24.12.0, npm 11.6.2; clean `npm ci` and the CI `--omit=optional` dependency-tree check passed. The current development tree contains 4 optional WASM packages reported as extraneous | Verified for reproducible install; current `node_modules` is not an exact clean-tree snapshot |
| Frontend tests | 32 test files and 335 tests passed on exact commit `a8e2f18` | Verified; existing React `act(...)` warnings remain non-blocking |
| Production build | Next.js 16.2.12 build completed and generated 71 routes | Verified; broad NFT trace and dependency-origin `url.parse()` warnings remain |
| Python runtime | Worktree-local Python 3.11.9 venv exists with CI requirements, pytest, FastAPI, LightGBM, pandas and scikit-learn | Verified |
| Python tests | Full `python-api/tests` suite passes 1,169 tests on exact commit `31c6b64`; accepted-evaluator runtime/configuration passes 31 tests, the source-readiness audit passes 8, and feature consistency passes 73 | Locally verified on worktree Python 3.11.9 and confirmed by exact-SHA remote CI |
| Local configuration | Ignored `.env` and `.env.local` contain local dummy endpoints and fail-closed write/scheduler/Saga switches; no secrets were copied | Ready for local health/fixture smoke, not authenticated hosted flows |
| Local operational data | A new empty 36 KiB `keiba/data/keiba_ultimate.db` fixture was initialized through the repository storage code | Ready for schema/startup smoke; real scrape/train/predict data remains absent |
| Services and E2E | FastAPI `/health` and Next.js `/api/health` returned 200; FastAPI OpenAPI and Next home returned 200; both unauthenticated protected-API probes returned 401; public fixture Playwright smoke passed 5/5; services were stopped afterward | Local integration slice verified |
| Dependency security | `@google-cloud/vision` 5.3.7 removes the vulnerable Google Vision/uuid path; `tsx` 4.23.1 and Vitest 4.1.10 move esbuild to 0.28.1. Full and production audits now contain 0 findings at every severity, and the mandatory dependency-tree install/check passes | Cleared locally and by exact-SHA remote CI |
| Model acceptance path | Strict row observations are recomputed into digest-bound evidence; the authorized owner approved every canonical threshold in GitHub issue #25; a one-shot Staging/Sandbox evaluator accepts only a fresh approved-contract report and registers it through the existing CAS RPC | Parent DB lacks eight required source capabilities; no real accepted evaluation or trusted Phase 3N run exists, and database evaluation remains structurally non-promoting |

Practical readiness:

- source review and frontend/contract-test work: approximately **90% ready**;
- self-contained local smoke execution: approximately **70% ready**;
- isolated Staging/Production operation: governed by the separate 30% operational-proof score below.

The parent worktree assets were not copied. This worktree now has independently generated local-only configuration, a Python 3.11 venv, and an empty schema fixture. Secrets and production data remain absent by design.

Exact-SHA CI run `30741847311` passed all 12 jobs for commit `31c6b6478dffda6e810af448e141b4089866e23f`, including dependency security, Python, Frontend, Playwright, scanners, Phase 3G-J runtime gates, the two-database Phase 3M bootstrap replay, and both container builds. The repository-quality pillar is therefore rescored from 80% to 88%, making the authoritative overall value 67.65% (reported as 68%). This is not a release authorization.

### 0.2 Historical external governance snapshot (2026-08-02)

GitHub metadata was inspected without changing repository or provider state on 2026-08-02:

| Boundary | Observed state | Assessment |
|---|---|---|
| Protected approval Environments | `staging-migration`, `staging-execution-unlock`, and `production-release` exist with required reviewer rules and explicit branch policies | Governance skeleton exists |
| Trusted producer | Immutable v1-v3 remain protected historical evidence; this candidate rotates consumers to `security/phase3n-trusted-producer-v4` | v4 must pass its protected review CI, be locked after merge, and be recorded in `PHASE3N_TRUSTED_PRODUCER_SHA` |
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
| ML and business-value proof | 25% | 55% | 13.8% | Strict training-only annual speed-deviation walk-forward records Spearman 0.8025 and calibrated winner AUC 0.8047, passing Brier/ECE/sample/bet checks, but still fails AUC, ROI (-23.23% vs +3%), maximum drawdown, and baseline ROI delta (-2.76 points vs +1); the old AUC 0.8865 is not current-artifact proof |
| Repository safety and quality gates | 25% | 88% | 22.0% | PR #28 implementation commit `cbae079` passes Python 1,185 locally, zero-vulnerability audits, and all 13 exact-SHA CI jobs in run `31801904942` |
| Staging and Production operational proof | 20% | 90% | 18.0% | Isolated providers, trusted evidence, credential rotation with old-key rejection, reviewed Production 21-migration adoption, Auth/RLS/IDOR, and maintenance recovery pass; controlled exact-candidate Production deployment/smoke and business observation remain open |
| **Overall** | **100%** |  | **77.15% authoritative** | Report as 77%; the system evidence and Production database gates pass, while Production remains NOT_READY until the controlled observation-only deploy and smoke/monitoring gates complete |

Workflow scoring assigns 1.0 point to `complete`, 0.6 to `partial`, and 0 to `missing`. Thus $(6 + 7 \times 0.6) / 13 = 78.5\%$, conservatively reported as 78%. Other pillar scores are evidence-based assessments and must be revisited when their exit conditions change.

### 3.1 Interpretation

- **Product implementation:** approximately 78%.
- **Repository-level safety and quality:** approximately 88%.
- **Real-environment readiness:** approximately 90%.
- **Overall goal:** **77.15%, reported as 77%**, with a reasonable uncertainty range of **75-80%**.

The score now counts the externally proven HA/fencing exercise, the first exact-SHA prospective observations, timestamped nonempty cache integrity, and successful signed trusted system run `31928529807`. It does not count unsettled predictions as valid business samples or an unexecuted Production deployment.

The revised policy also reports a separate Limited Production system-release
indicator without the deferred ML/business-value pillar:

`(78% * 30% + 88% * 25% + 90% * 20%) / 75% = 84.53%`

This is the evidence-based current value, so the informal 85-90% estimate is
not yet adopted. Monitoring/alerting, executable Production rollback, critical
operator runbooks, trusted-producer parity, exact authorization, and Production
smoke must pass before that system-only score can advance. The original 74.15%
continues to measure the complete project through model business validation.

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
- Operational saga and non-synthetic multi-instance Staging proof exist; Production monitoring, incident, and rollback execution remain open.
- Historical model quality is encouraging and the business acceptance criteria are approved, but fresh current-commit evidence is incomplete.

### 3.4 What blocks each release class

Limited Production observation remains blocked by:

1. Review/merge of the exact observation-release contract and runtime controls.
2. Immutable trusted-producer update/re-review and a GitHub-signed system attestation for the exact candidate.
3. Production monitoring/alerting, incident ownership, rollback rehearsal, environment binding, and final smoke evidence.
4. Protected Production release approval. Provider deployment remains a separate explicit operation.

Model `validated`/`active` remains blocked by:

1. AUC 0.7581 versus 0.85 and insufficient prospective ROI/MaxDD evidence.
2. Zero settled valid samples, qualifying settled bets, and elapsed settled days versus 1,000/100/90.
3. Trusted model-evaluation payload, accepted-candidate registration, and the full Phase 3H READY decision.
4. A later independent activation approval; automatic betting is not authorized by either observation release or model validation alone.

---

## 4. Definition of Done

Limited Production observation is complete when the system-release controls in
`docs/limited_production_observation_release.md` pass. The complete project
reaches 100% only when every gate below, including model business validation,
is satisfied.

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
| WP0 Canonicalize current evidence | Sysop | none | **Complete for the approved-policy candidate:** runtime commit `a70ef54b282d8a7057938b3d74226b6ce0cf60f2` passes Python 1,186 locally and all 13 CI jobs in run `31811447458`; evidence-recording commit `130c72484a6e2afe46cf37b384e27b4ea8f4c08d` passes all 13 jobs in run `31812943165`, is deployed with a matching candidate SHA, and passes the hosted health/auth regression | Preserve the deployed runtime identity through merge; historical reports remain explicitly labeled non-current | +2% |
| WP1 Define business acceptance contract | Jobs + Trainer + Ledger | none | **Contract approved:** the authorized owner approved every threshold and the out-of-time/no-leakage/staking policy in GitHub issue #25. The versioned fail-closed contract, verifier, abuse tests, CI/trusted Phase 3N wiring, and sanitized source audit are implemented. The parent DB cannot supply the required strict observations | Fresh current-commit out-of-time evidence passes the approved attested promotion gate | +5% |
| WP2 Finish operator workflow gaps | Harvester + Trainer + Oracle | WP1 for model decisions | **In progress:** quality bridge, authenticated Admin profiling viewer, read-only feature provenance/INV-01 catalog, strict retrain payload hashing, an Admin-only eligibility assessment, deployed-environment blocks on legacy model training/activation/deletion/repair, an Admin request/independent-decision/job-status panel, private two-person approval/job ledgers, service-only CAS/lease/fencing transitions, immutable private-bucket artifact/evaluation registration, a service-only execution bundle, an isolated OOT trainer, and a fail-closed one-shot coordinator are implemented. A bounded one-shot dispatcher selects at most five exact candidates and delegates to the fenced coordinator; a one-shot evaluator rebuilds evidence from strict rows in memory, requires the canonical approved contract and accepted verifier report, and records it through CAS while keeping promotion false. A separate reconciler handles expired leases and old unregistered exact-name objects with an immutable outcome ledger. Deployment and recurring scheduling, hosted PostgreSQL/Storage runtime evidence, trusted Phase 3N attestation and candidate comparison, separate switch/retirement approvals, standalone generation execution, and advanced evaluation remain | Accepted workflows meet G1; intentionally script-only items have runbooks | +6% |
| WP3 Provision isolated Staging governance | Sysop | WP0 | **Complete:** isolated Vercel/Render/Supabase identities and protected approval boundaries are established; exact Render deployment identity was revalidated and the service remains one Free instance after the authorized HA exercise | Preserve the isolated identities and Free final state | +4% |
| WP4 Apply and verify hosted bootstrap | Sysop | WP3, explicit migration approval | **Complete:** isolated Supabase Staging retains ordinals 1-19 and has append-only ordinals 20-21. Read-only verification reports 21 history rows, both Phase 3N additions, required RPC/table presence, service-role execution, and anon denial | Keep the append-only history invariant through evidence collection | +4% |
| WP5 Run Staging security and model checks | Sysop + Trainer + Oracle | WP4 | **Partial:** hosted Auth/RLS/IDOR, approved staking/payout, exact-commit deployment, public health/OpenAPI 200, protected API 401, and the first exact-SHA `middle` prediction batch pass. No settled row or acceptance-complete model exists | G2 security boundary and all approved model thresholds pass on real candidate data | +4% |
| WP6 Run bounded operational exercise | Harvester + Sysop | WP4 | **Substantially complete:** bounded HTTP/no-mutation, rollback, real two-instance crash/recovery/fencing, exact-commit CI HA, hosted health/auth regression, and nonempty cache delete/rebuild/digest evidence pass. Final trusted-evidence assembly is deferred until the prospective window completes | Every Phase 3N saga/staging boolean is supported by sanitized non-synthetic evidence | +5% |
| WP7 Produce trusted Phase 3N evidence | Sysop | WP5, WP6, three approvals | **Complete for limited system release:** protected run `31928529807` produced GitHub-signed exact-candidate artifacts and independently derives `trusted=true`, `l3_eligible=true`, and `production_ready=true`; repository selectors pin the immutable run and producer | Preserve provenance and selectors; full model-business evidence remains a later, separate gate | +3% |
| WP8 Promote and observe Production | Sysop + Jobs + Ledger | WP7, release approval | Controlled release, monitoring evidence, rollback readiness, business observation report | G5 passes and agreed observation period completes | +4% |

The percentages above are prioritization estimates, not additive score increments. They intentionally overlap across pillars; completion must trigger a fresh pillar-by-pillar score instead of summing the values. They do not authorize release by themselves.

### Immediate next sequence

1. **Complete:** review and merge of the limited observation contract, runtime guards, workflow split, health-mode projection, and runbook with an unchanged candidate tree.
2. **Complete:** immutable trusted producer v4 and protected run `31928529807` establish signed limited-system READY for exact candidate `86a2d314...`; selectors pin both identities.
3. Finish Production health/error/observation alerts and incident ownership; configure Production with observation enabled last and automatic betting disabled. The rollback mechanism is already non-synthetically proven in Staging.
4. Run the protected limited release authorization, deploy the exact merge, and pass health/auth/prediction/append-only observation/rollback smoke without enabling automatic betting.
5. After race `202604020812` is authoritative, reconcile its result exactly once and continue prospective collection to 90 days, 1,000 settled samples, and 100 qualifying virtual bets.
6. Improve and evaluate the market-free model only on the predeclared schedule, freeze Inner policy before Outer inspection, and require all business gates before `validated`; require a later separate approval before `active`.

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
| 2026-08-02 | `f2614e4` isolated Staging rollout | 73.65% authoritative / 74% reported | NOT_READY | Exact-SHA CI, isolated Vercel/Render/Supabase identities, fresh 19-migration bootstrap, hosted Auth/RLS/IDOR, bounded HTTP/no-mutation, and rollback evidence passed. Persistent-cache, multi-instance HA/fencing, model observations, and trusted evidence remained open. |
| 2026-08-14 | `f5b9c90` hosted HA/fencing baseline | 75.65% authoritative / 76% reported | NOT_READY | The authorized Render two-instance Starter exercise proved real claim/crash/recovery, lease takeover, stale-fence rejection, and cleanup; Render was then returned to one Free instance. No Production change was made. |
| 2026-08-14 | `cbae079` PR #28 observation/evidence candidate | 75.65% authoritative / 76% reported | NOT_READY | Deterministic fail-closed staking/payout, append-only result reconciliation, and a bounded cache-integrity evidence command are implemented. Local Python 1,185, zero-vulnerability audits, all 13 exact-commit CI jobs in run `31801904942`, and both Vercel checks pass. The score does not count pending Staging regression, issue #29 approval, uncollected 90-day observations, or trusted evidence as complete. |
| 2026-08-14 | Issue #29 staking/payout approval | 75.65% authoritative / 76% reported | NOT_READY | Repository owner `yuki20001105` approved the exact `phase3n-tansho-flat-v1` policy. The durable comment reference is now bound into the tracked policy candidate. The score remains unchanged until exact-commit Staging regression/cache evidence and real observations pass. |
| 2026-08-15 | `a70ef54` approved-policy exact-commit Staging regression | 75.65% authoritative / 76% reported | NOT_READY | Local Python 1,186 and all 13 jobs in CI run `31811447458` pass. Render is Live at exact `a70ef54`, its candidate-SHA/observation/expanding-window/Staging boundaries are aligned, and hosted `/health` plus OpenAPI return 200 while the protected scrape-health API returns 401. Supabase retains all 21 append-only history rows and exposes the service-only Phase 3N schema, but prediction/result counts remain zero; cache integrity and the observation clock therefore remain honestly open. |
| 2026-08-15 | `130c724` evidence-recording exact-commit Staging checkpoint | 75.65% authoritative / 76% reported | NOT_READY | The documentation-only follow-up passes all 13 jobs in CI run `31812943165` and is Live on Render with `PHASE3N_CANDIDATE_COMMIT_SHA` aligned to the same full SHA. Post-deploy `/health` and OpenAPI return 200 and the protected scrape-health API returns 401. Render remains one Free instance. The Supabase ledger still has zero prediction/result rows, so no cache-integrity success or observation start is claimed. |
| 2026-08-15 | `961a5be` ephemeral-SQLite startup checkpoint | 75.65% authoritative / 76% reported | NOT_READY | Render Free now creates required SQLite tables before serving the first request, is Live at the exact commit with the candidate SHA aligned, and returns healthy. All 13 jobs in CI run `31815636708` pass. The first real 18-runner request was correctly rejected before capture because official odds/popularity were not yet published; the Staging ledger therefore remains at zero rows. |
| 2026-08-15 | Current-artifact historical OOT replay | 73.15% authoritative / 73% reported | NOT_READY | Mixed-generation `finish_position` rows exposed a row-level coalescing defect, which is regression-fixed. A strict complete-race replay over 2026-02-02 through 2026-07-11 (980 races, 10,768 entries, 160 days) passes Brier 0.0791, ECE 0.0265, drawdown 3.60%, bet/sample/period minimums, and baseline ROI delta +12.71 points, but fails AUC 0.7158 and ROI -8.27%. This is real historical screening data, not prospective or trusted Phase 3N evidence. |
| 2026-08-15 | Ten-year speed-deviation regression screen | 73.15% authoritative / 73% reported | NOT_READY | The first 2016-2026 candidate exposed `time_index` and current-race lap/pace leakage and was quarantined without activation. The corrected candidate uses 267,700 training entries across 23,495 races, a training-only distance/surface target baseline, five-year recency half-life, and 1,293 complete OOT races. It records RMSE 0.6947, Spearman 0.7990, winner AUC 0.7802, Brier 0.0791, ECE 0.0420, ROI -14.81%, drawdown 21.42%, and +4.15-point ROI delta to the favorite baseline. Data are dense in 2016-2018 and 2025-2026 but sparse in 2019-2024, so the candidate remains unapproved and undeployed. |
| 2026-08-15 | Strict annual speed-deviation walk-forward | 73.15% authoritative / 73% reported | NOT_READY | Four outer years (2016, 2017, 2018, 2025) are never used for iteration, feature, temperature, or wagering-policy selection. Aggregate evaluation covers 264,705 entries and 23,208 complete races with RMSE 0.6069, Spearman 0.8025, calibrated AUC 0.8047, Brier 0.0694, and ECE 0.0045. The approved EV>=1.2 flat-win policy produces 21,823 wagers, ROI -23.23%, drawdown above 20%, and -2.76-point delta to the favorite baseline; AUC/ROI/drawdown/delta therefore fail. The earlier single-window ROI is retained only as historical development evidence because it forced one wager per race instead of enforcing the approved EV threshold. No model was activated or deployed, and data after 2026-07-11 remain reserved. |
| 2026-08-15 | Licensed-history and nested OOF value implementation | 73.15% authoritative / 73% reported | NOT_READY | The research path now has a hash-verified append-only 2019-2024 import contract, canonical point-in-time odds selection, an OOF-only winner meta-model, and inner-meta-OOF-only strategy search. A read-only audit of the current DB records 576/548/830/0/0/42 legacy entries for 2019-2024, respectively, but zero licensed entries and zero timestamped odds entries in every year. No authorized export was fabricated or downloaded, no DB was modified, no outer evaluation was claimed, and all generated candidates remain unapproved and undeployed. |
| 2026-08-15 | Free JRA official-history full evaluation | 73.15% authoritative / 73% reported | NOT_READY | All 1,729 official 2019-2024 PDFs were hash-recorded and converted into 273,187 timed runners across 19,846 flat races; 755 jump races, 87 unreadable/incomplete flat layouts, and three unsupported-font documents are explicitly audited rather than guessed. The identical manifest passed a no-write 273,187-record dry-run and append-only import to a research DB copy; `quick_check=ok` and every year exceeds 3,281 outcome races. The initial corrected 2020-2024 expanding evaluation excluded direct odds but reported AUC 0.7137. A subsequent audit removed six remaining derived market/payout fields, added strictly shifted prior-speed histories, and trained the winner probability layer only from inner-period OOF speed scores and pre-race context. The 229,190-entry / 16,644-race v2 report reaches AUC 0.7581, Brier 0.06260, and ECE 0.00301 with zero future/market intersections, but still fails 0.85; ROI/DD remain unavailable. No model was approved, activated, or deployed. |
| 2026-08-15 | `fc56d22` live-input exact-SHA rollout and market-free OOF-meta baseline | 73.15% authoritative / 73% reported | NOT_READY | All 13 remote CI jobs pass and the same SHA is Live on one Free Render instance; hosted health returns 200, unauthenticated analyze returns 401, and the next-race `yoso` quote is rejected without an observation write. The research-only v2 annual evaluation adds shifted horse-speed histories and an inner-period OOF winner meta-model, improves AUC from 0.7137 to 0.7581, and records zero future or market-feature intersections. The AUC and prospective ROI/evidence gates remain open, so neither the candidate nor Production changed. |
| 2026-08-15 | `3d24948` Phase3N nested-value hardening | 73.15% authoritative / 73% reported | NOT_READY | The existing OOF value path now requires complete fresh `middle` quotes, source, and one exact SHA; rejects final/result, stale, mixed-SHA, and post-start inputs; keeps the winner meta-model market-free and strategy selection Inner-only; and enforces one wager per race, 5-15% realized bet rate, minimum count, drawdown, baseline delta, year stability, ROI confidence bounds, and adjacent-grid robustness. Reports bind the approved policy, search space, decision, and Inner/Outer input rows by SHA-256. Local FastAPI 1,196 plus research 118 tests pass with 6 research skips; both safety scanners report zero findings. All 13 jobs in CI run `31884370561` and both Vercel checks pass. No genuine point-in-time dataset was created, no ROI result was claimed, and no model was approved or deployed; the overall score therefore remains 73.15%. |
| 2026-08-15 | `2d39111` first exact-SHA prospective observation and cache evidence | 74.15% authoritative / 74% reported | NOT_READY | The result-unknown race `202604020812` had `middle` status and 15 complete actual odds. Normal authenticated Staging analysis returned 200 and wrote 15 predictions joined to exact SHA `2d39111...`. A separate earlier 15-row run bound to legacy SHA `961a5be...` remains append-only audit history and is excluded from current-candidate counts. The authorized non-paid cache exercise used all 30 PostgreSQL rows and proved identical DB/cache digest `46ff587b...a39a0e`, database unchanged, and zero missing/duplicate/stale cache rows. Local Python 1,325 passes with 6 skips; all 13 jobs in CI run `31890053180` pass after an unrelated Playwright timing flake passed unchanged on attempt 2. Settled progress remains 0/1,000 samples, 0/100 bets, and 0/90 days; AUC remains 0.7581, so Production stays NOT_READY. |
| 2026-08-16 | Limited Production observation contract candidate | 74.15% authoritative / 74% reported; 80.53% system-release indicator | NOT_READY | System release and model business validation are now separate executable gates. The candidate adds an exact-commit trusted-system verifier, an explicit `limited-observation` workflow path, Production observation boundary checks, non-secret health mode reporting, and a hard runtime denial of live betting unless a future independently approved `active` model explicitly opts in. Full model validation remains the workflow default. Local Python 1,211, workflow YAML parsing, secret scanning, test-weakening scanning, and diff checks pass. This implementation does not deploy or authorize Production: PR review/merge, trusted-producer parity/review, monitoring and rollback readiness, protected Production approval, exact-commit deploy, and Production smoke evidence remain required. |
| 2026-08-16 | `86a2d314` trusted-evidence prerequisite audit and warm split | 74.15% authoritative / 74% reported; 80.53% system-release indicator | NOT_READY | Exact-SHA real-race analysis and cache integrity remain valid. Cold wake measured 31.868 s; the wake-following full analysis measured 54.436 s; six successful authenticated warm-cache responses measured 391-974 ms (P50 413 ms, P95 974 ms) before HTTP 429, so the 500 ms isolated prediction gate is not claimed. GitHub Environment policies now admit immutable producer v4. Protected B64 dispatch is intentionally blocked because the current operational 21-row Supabase history is not a fresh single-manifest bootstrap, no Preview Branch exists, the retained hosted HA job is explicitly synthetic, and complete non-synthetic rollback/Auth-RLS-IDOR evidence plus cache capture timestamps are absent. No secret was registered, no trusted workflow was dispatched, no paid provider setting or Production resource changed. |
| 2026-08-16 | `86a2d314` non-synthetic HA and rollback evidence | 74.15% authoritative / 74% reported; 80.53% system-release indicator | NOT_READY | A disposable Supabase Preview Branch received all 21 exact-SHA migrations under manifest `99be317b...e557`. Render ran two Starter instances for 492 seconds: a real cache-rebuild lease owner was SIGKILLed, the peer reclaimed the expired lease, applied one effect, and the stale owner was fenced. Render was restored to the exact pre-test environment digest, one Free instance, and healthy observation-only runtime at the exact candidate; the Preview Branch was deleted after 3,190 seconds. Production and automatic betting were unchanged. The score remains fixed until the complete trusted system-observation envelope and protected workflow pass. |
| 2026-08-16 | Protected trusted limited-system run `31928529807` | 76.15% authoritative / 76% reported; 83.20% system-release indicator | NOT_READY | Immutable producer `c8308a3...` accepted the sanitized exact-SHA system observation after three Environment approvals. All five jobs passed; signed artifact digests, independent local verification, Auth/RLS/IDOR, 75-row timestamped cache integrity, provider identities, 21-migration proof, non-synthetic HA/rollback, and every saga/staging check pass. The gate derives `trusted=true`, `l3_eligible=true`, and `production_ready=true` with zero failure codes. Repository selectors pin the successful run and producer. Production remains unchanged and NOT_READY pending the controlled observation-only deploy, monitoring, and smoke gates. |
| 2026-08-16 | Production operational preflight | 76.15% authoritative / 76% reported; 83.20% system-release indicator | NOT_READY | Production Supabase was explicitly authorized and resumed without a DB write. Auth health, REST schema, and bounded Auth Admin reads return 200; the service-role claim binds `grfwkutcsavqicaimssn`. Migration history is empty and only 29 legacy REST paths exist, with all seven Phase3N observation/HA tables absent, so schema readiness fails closed. Authenticated Vercel review separated Production `keiba-ai-pro` (`prj_UcRc...`) from Staging `keiba-ai-pro-staging` (`prj_PRd5...`), found Production still on `69dd9e9...`, no native Hobby alerts, no E2E variables, an all-environments non-Sensitive legacy service-role key distinct from the Staging keys, and none of the required exact-SHA/contract/observation/betting/training/activation safety variables. The initial `/api/health` 503 completed at the route's four-second backend timeout; after Render warmed, three checks returned 200 in 734-1,194 ms, so cold-start-sensitive monitoring remains open. The gate also remains closed because old E2E password rejection, Production migration approval/application, Production Render ownership/runtime SHA, application-specific alerts, and a provider-bound healthy rollback SHA are unresolved. |
| 2026-08-16 | Legacy Production schema adoption review | 76.15% authoritative / 76% reported; 83.20% system-release indicator | NOT_READY | Read-only Production catalog/count/digest checks bound 24 legacy public tables and the non-empty datasets (3 profiles, 4 purchases, 288 races, 9,588 results, 5,847 payouts, 72 race blobs, 719 result blobs, 73 model metadata rows, 1,805 pedigrees, and one archived-only legacy user). All checked parent/Auth/Storage links pass; the three double-encoded JSON datasets decode to objects and all result horse identities are complete and unique. A deterministic review bundle now archives legacy tables without deletion, builds the exact 21-migration canonical schema, converts/copies allowlisted rows, and checks archived digests and destination counts in one transaction. It is unconditionally blocked before the first change while `migration_apply_authorized=false`; the standalone preflight is repeatable-read/read-only. Production was not migrated, and the score remains unchanged pending review, disposable-clone execution, backup/restore evidence, and separate explicit migration approval. |
| 2026-08-22 | Production credential rotation and 21-migration adoption | 77.15% authoritative / 77% reported; 84.53% system-release indicator | NOT_READY | A new Production Supabase server secret replaced the legacy service-role credential in Vercel Production, Render, and GitHub Actions. Vercel redeployed the same known runtime with the secret Production-scoped and Sensitive; Render redeployed `62748ce...`; legacy JWT API keys were disabled, the old server key returned 401, and the new secret, publishable Auth, Vercel health, and Render health returned 200. A temporary management token was deleted immediately. Under explicit approval, Render was suspended for maintenance, the latest physical backup and read-only source-digest preflight were verified, and apply SQL `42249f1a...20a7` committed as one transaction. Independent audit records 21 exact-candidate history rows, 24 archived legacy tables, 45 canonical public tables, zero missing RLS, zero anon table privileges, 19 policies, four Auth users, 146 model Storage objects, and the Phase3N observation table. Render resumed healthy. The exact Limited Production candidate is still undeployed, so Production remains NOT_READY. |

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
- `docs/limited_production_observation_release.md`: system-release/model-validation split, observation-only controls, monitoring, and rollback.
- `docs/phase3n_legacy_production_schema_adoption.md`: fail-closed legacy Production preflight, archive-and-rebuild SQL, data preservation, and rollback gates.
- `docs/repair_execution_policy.md`: fail-closed direct-repair boundary and prerequisites for future approval-bound execution.

Historical documents may contain stale versions or assumptions. When they conflict, prefer `docs/specs/SYSTEM.md`, executable current-commit evidence, and this status document.
