# Model Retrain Worker Runbook

Updated: 2026-08-02
Status: repository-ready procedure; not yet executed against hosted Staging

## Purpose and boundary

This runbook covers the one-job worker, the bounded dispatcher that invokes it, and orphan reconciliation in isolated Staging or Sandbox. The worker claims exactly one job and expected CAS version, retrieves the service-only execution bundle, copies and verifies the immutable SQLite snapshot, trains in a temporary workspace, uploads one digest-named artifact to the private `models` bucket, and registers it under the live fencing token.

The worker itself does not poll the queue. The separate dispatcher performs one bounded read-only queue scan and invokes the same fenced coordinator sequentially. Neither component evaluates or promotes a candidate, switches the active model, retires an old model, or authorizes Production. A successful worker or dispatch pass therefore ends at `artifact-registered`, not `Production READY`.

## Required evidence before execution

- The candidate full commit SHA is deployed and matches the approved dry-run payload.
- All 19 canonical Phase 3M migrations, including `20260802_model_retrain_execution_bundle.sql`, `20260802_model_retrain_orphan_reconciliation.sql`, and `20260802_model_retrain_dispatch_queue.sql`, have passed the approved Staging bootstrap gate.
- The Staging Supabase project and private `models` bucket are isolated from Production; service-role credentials are stored only in the protected worker environment.
- A two-person approval exists, is unexpired, and has produced one queued job with its current `job_id` and `version`.
- `python-api/models/.active_model.json` and its referenced local `.joblib` are provisioned from the approved Staging active model; their model ID matches the approval binding.
- The snapshot is an absolute, immutable `.db` file between 1 byte and 10 GiB. Its SHA-256 exactly equals the approved `data_snapshot_id`.
- The approved training and validation periods do not overlap, and every required safety check is `pass`.
- Exact-SHA CI is green. Approval Environment reviewers have authorized the bounded execution window.

Do not copy Production secrets or a mutable Production database into the worker. Do not use the local empty fixture as model evidence.

## Worker configuration

Set these values in the protected worker process, without printing their values:

| Variable | Required value |
|---|---|
| `APP_ENV` | `staging` or `sandbox` |
| `MODEL_RETRAIN_EXECUTION_ENABLED` | Exact string `true` |
| `MODEL_RETRAIN_WORKER_ID` | Stable 3-80 character worker/attempt identifier |
| `MODEL_RETRAIN_JOB_ID` | Approved queued job UUID |
| `MODEL_RETRAIN_JOB_VERSION` | Current positive CAS version read immediately before dispatch |
| `APP_COMMIT_SHA` | Exact 40-character lowercase deployed commit SHA |
| `MODEL_RETRAIN_SNAPSHOT_PATH` | Absolute path to the approved immutable `.db` snapshot |
| `MODEL_RETRAIN_LEASE_TTL_SECONDS` | 30-300; default 120 |
| `SUPABASE_URL` | Isolated Staging/Sandbox project URL |
| `SUPABASE_SERVICE_KEY` | Protected service-role credential |

The runner derives `staging-train` or `sandbox-train` from `APP_ENV`; it cannot be overridden independently. It also obtains the active model ID from the provisioned local active-model file and fails closed when the file or referenced artifact is absent.

## Preflight

From the exact deployed checkout:

```powershell
git rev-parse HEAD
git status --short
Get-FileHash -Algorithm SHA256 -LiteralPath $env:MODEL_RETRAIN_SNAPSHOT_PATH
& python-api/.venv/Scripts/python.exe -m pytest python-api/tests/test_retrain_worker.py python-api/tests/test_retrain_worker_main.py -q
```

Compare the Git SHA and snapshot digest to the approved ledger values out of band. `git status --short` must be empty. Never place service credentials or their values in an evidence artifact.

## Bounded execution

Run one process for the one approved job:

```powershell
& python-api/.venv/Scripts/python.exe python-api/retrain_worker_main.py
```

Success emits a sanitized JSON object containing `job_id`, artifact SHA-256, size, media type, and private object name. Failure emits only `{"success": false, "code": "retrain-worker-failed"}` and exits non-zero. Diagnose through protected platform/database audit logs, not by weakening response sanitization.

## Required postconditions and evidence

- Job state is exactly `artifact-registered` with the expected worker ID, current fencing token, monotonically increased version, and one immutable registration row.
- The object name is exactly `retrain/<job_id>/<sha256>.joblib` in the private `models` bucket; its size and digest match the registration.
- Approval and job audit events show claim, heartbeats, start, and registration in chronological order.
- No legacy `model_metadata` row, active-model pointer, evaluation, promotion, or Production resource changed.
- The snapshot, worker logs, RPC observations, object metadata, commit SHA, reviewer approval references, and timestamps are archived as Staging evidence without secret values.

Only after these checks may the separate accepted evaluator process observations bound to the registered artifact. Evaluation acceptance still does not authorize promotion; the trusted Phase 3N producer remains a later boundary.

## Accepted evaluation registration

Use the one-shot evaluator only after the artifact is registered, the business acceptance contract is durably approved, and the reviewed strict OOT row set is available in the protected Staging/Sandbox process. Set:

| Variable | Required value |
|---|---|
| `APP_ENV` | `staging` or `sandbox` |
| `MODEL_RETRAIN_EVALUATION_ENABLED` | Exact string `true` |
| `MODEL_RETRAIN_EVALUATOR_ID` | Stable 3-80 character lowercase evaluator identity |
| `MODEL_RETRAIN_JOB_ID` | Exact artifact-registered job UUID |
| `MODEL_RETRAIN_JOB_VERSION` | Current positive CAS version read immediately before evaluation |
| `APP_COMMIT_SHA` | Exact nonzero 40-character lowercase candidate commit SHA |
| `MODEL_RETRAIN_OBSERVATIONS_PATH` | Absolute non-symlink strict OOT observation JSON |
| `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` | Protected isolated-project service credentials |

Run one evaluation pass:

```powershell
& python-api/.venv/Scripts/python.exe python-api/retrain_evaluator_main.py
```

The evaluator never accepts aggregate metrics from the caller. It rebuilds metrics and digests from strict rows in memory, uses only the repository's canonical contract, requires an approved/fresh accepted verifier report, then independently checks the sanitized projection before invoking `register_model_retrain_accepted_evaluation`. The database rechecks the current approval, artifact digest, candidate commit and CAS version. Success moves only `artifact-registered` to `evaluation-recorded`; `trusted_promotion_evidence` and `promotion_eligible` remain false. A draft contract, stale rows, threshold failure, binding mismatch, or malformed RPC response exits with a generic sanitized error and no successful registration.

Do not retain raw rows or aggregate evidence in ordinary command output. Preserve the reviewed source set in the approved evidence system by its observation digest, and retain only the sanitized report/job event for the operational record. This evaluator is not the trusted Phase 3N producer and cannot authorize a switch.

## Bounded queue dispatch

Use the dispatcher only as a separately enabled, one-shot Staging/Sandbox process after the 19th migration is applied. Every approved snapshot must be provisioned as an immutable, non-symlink file named `<approved-data_snapshot_id>.db` in one absolute catalog directory. Set:

| Variable | Required value |
|---|---|
| `APP_ENV` | `staging` or `sandbox` |
| `MODEL_RETRAIN_DISPATCH_ENABLED` | Exact string `true` |
| `MODEL_RETRAIN_DISPATCHER_ID` | Stable 3-50 character lowercase dispatcher identity |
| `APP_COMMIT_SHA` | Exact nonzero 40-character lowercase deployed commit SHA |
| `MODEL_RETRAIN_SNAPSHOT_DIRECTORY` | Absolute immutable snapshot catalog directory |
| `MODEL_RETRAIN_DISPATCH_LIMIT` | 1-5; default 1 |
| `MODEL_RETRAIN_LEASE_TTL_SECONDS` | 30-300; default 120 |
| `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` | Protected isolated-project service credentials |

Run one bounded pass:

```powershell
& python-api/.venv/Scripts/python.exe python-api/retrain_dispatcher_main.py
```

The read-only projection returns only queued, unleased, unstarted and unregistered jobs with a live independent approval, all required safety checks, and exact policy/commit/active-model bindings. Python independently rejects malformed, duplicate, oversized, cross-policy, cross-commit, cross-model, invalid-version, or zero-digest results before any snapshot lookup or claim. Missing or mismatched catalog snapshots are skipped without claiming. Eligible jobs are then processed sequentially through the existing CAS/fence/heartbeat coordinator.

Archive the sanitized candidate/dispatched/skipped/failed counts with platform logs and the immutable job event ledger. The dispatcher does not yet have a separate durable scan ledger, so command output alone is not trusted Phase 3N evidence.

## Failure and recovery

- Bundle, policy, commit, active-model, snapshot, or feature mismatch: keep the job failed; create a new approval/job after correcting the immutable input. Do not edit the approved record.
- Lease loss or expiry: the coordinator cancels cooperatively and must not upload/register using a stale fence. Recover through the fenced job recovery RPC and a new worker attempt/version.
- Training failure: preserve sanitized failure/audit evidence; do not reuse a partially produced local artifact.
- Upload succeeds but registration fails: the coordinator attempts to remove the unregistered object before reporting failure.
- `retrain-orphan-cleanup-required`, process crash, or host loss after upload: stop dispatching and obtain operator review before running the bounded reconciler. Do not manually delete by path alone.
- Any unexpected active-model, evaluation, promotion, or Production mutation: stop the exercise, preserve evidence, revoke the execution window, and follow the incident/rollback procedure.

## Bounded orphan reconciliation

Use the reconciler only in a separate protected Staging/Sandbox process after the 18th migration is applied. Set:

| Variable | Required value |
|---|---|
| `APP_ENV` | `staging` or `sandbox` |
| `MODEL_RETRAIN_ORPHAN_RECONCILIATION_ENABLED` | Exact string `true` |
| `MODEL_RETRAIN_RECONCILER_ID` | Stable 3-80 character reconciler identity |
| `MODEL_RETRAIN_ORPHAN_MIN_AGE_SECONDS` | 3600-604800; default 3600 |
| `MODEL_RETRAIN_RECONCILIATION_LIMIT` | 1-50; default 20 |
| `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` | Protected isolated-project service credentials |

Run one bounded pass:

```powershell
& python-api/.venv/Scripts/python.exe python-api/retrain_reconciler_main.py
```

The pass first lists expired `claimed`/`running` jobs and invokes the existing CAS recovery RPC. Expired `claimed` work returns to `queued`; expired `running` work becomes terminal `failed`. It then deletes only objects older than the configured minimum age whose exact job is `failed`, has no artifact identity, and has no immutable registration. Registered, active, malformed, too-new, and unknown-job objects are excluded by the database projection and independently validated by Python.

Success and candidate-zero passes produce a sanitized run ID and counts. A deletion error is recorded as `delete-failed` before the command exits non-zero. Preserve the immutable reconciliation row, job lease-expiry event, platform Storage audit, and command output as one evidence set.

## Remaining automation gap

Before unattended Staging operation, deploy a protected scheduler around the one-shot dispatcher/reconciler, define retention and alerting for every pass, and exercise dispatcher, worker, crash recovery, reconciliation, PostgreSQL, and private Storage together in the isolated environment. Production execution remains prohibited until the full Phase 3N trusted evidence and separate release approval derive `production_ready=true` for the exact candidate.
