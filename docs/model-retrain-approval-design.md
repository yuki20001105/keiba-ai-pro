# Model Retrain Approval Design

Updated: 2026-08-02
Status: approval/job/artifact/evaluation contracts and a fail-closed one-shot retrain runner are implemented locally; hosted execution is not yet evidenced

## 1. Purpose and Non-goals

Purpose:
- Keep the approval target boundary immutable through job execution and artifact registration.
- Define immutable contracts for dry-run payload and approval record.
- Define what becomes executable only after approval.

Non-goals in this phase:
- No autonomous queue poller or production retrain execution.
- No overwrite of an existing `.joblib`, legacy model directory, or registered object.
- No `.active_model.json` mutation.
- No active model switch execution.
- No production/base table write enablement.

## 2. Dry-run Payload Schema (Fixed)

Entity name:
- `ModelRetrainDryRunPayload`

Required fields:
- `dry_run_id`: string (UUID-like)
- `generated_at`: string (ISO8601)
- `target`: string
- `model_type`: string
- `train_period`: `{ start: string|null, end: string|null }`
- `validation_period`: `{ start: string|null, end: string|null }`
- `feature_count`: integer (>=0)
- `selected_features`: string[]
- `removed_features`: string[]
- `expected_outputs`: string[]
- `estimated_runtime`: `{ unit: string, min: number, max: number, note: string }`
- `safety_checks`: `{ key: string, status: "pass"|"warn"|"fail", note: string }[]`
- `source_model_id`: string|null
- `active_model_id`: string|null
- `feature_contract_hash`: string
- `data_snapshot_id`: string
- `code_version`: string
- `git_commit`: string
- `created_by`: string
- `state`: `preview-ready|preview-warn|preview-fail`

Optional fields:
- `warnings`: string[]
- `notes`: string[]

Normalization rules:
- `feature_count` must equal `selected_features - removed_features` by unique set diff.
- `feature_contract_hash` is computed from normalized feature contract (sorted, deterministic).
- `data_snapshot_id` identifies immutable data basis used in preview.
- `git_commit` is a short commit sha or equivalent immutable revision marker.

## 3. Approval Record Schema (Fixed)

Entity name:
- `ModelRetrainApprovalRecord`

Required fields:
- `approval_id`: string
- `dry_run_id`: string
- `approved_by`: string|null
- `approved_at`: string|null
- `approval_status`: `pending|approved|rejected|expired|invalidated`
- `approval_comment`: string
- `approved_payload_hash`: string
- `requested_by`: string
- `requested_at`: string (ISO8601)
- `expires_at`: string (ISO8601)
- `invalidation_reason`: string|null
- `execution_policy`: `read-only-preview|staging-train|sandbox-train`
- `allowed_actions`: string[]

Allowed action examples:
- `submit_approved_retrain`
- `view_approval_status`
- `view_job_status`

Policy:
- Approval record is immutable except status transition fields.
- `approved_payload_hash` must be derived from canonical dry-run payload.
- Any mismatch invalidates execution eligibility.

## 4. Job Submission Preconditions (Approved-only)

`submit_approved_retrain` is allowed only when all are true:
- `approval_status=approved`
- current dry-run payload hash equals `approved_payload_hash`
- current time <= `expires_at`
- `active_model_id` unchanged since approval
- `feature_contract_hash` unchanged since approval
- `code_version` and `git_commit` unchanged since approval
- caller role is `admin` (or strict policy explicitly allowing premium)
- production/base write remains disabled
- model artifact write is allowed only for explicit `staging/sandbox` execution policy

Hard blocks:
- Expired approval
- Invalidated approval
- Any hash or identity mismatch
- Attempt to include path-like inputs

## 5. Updatable Targets After Execution (Separated)

After approved retrain execution, update domains are separated:
- `retrain_job_result`
- `generated_model_artifact`
- `evaluation_report`
- `comparison_report`
- `model_registry`
- `active_model_pointer`

Critical separation rule:
- `active_model_pointer` switch requires separate Admin approval, independent from retrain approval.

## 6. API Design

In-scope API contracts:
- `POST /api/model-redesign/summary` with `action=retrain_dry_run`
- `POST /api/model-redesign/approval` with `action=create_approval`
- `GET /api/model-redesign/approval/:approval_id`
- `POST /api/model-redesign/job` with `action=submit_approved_retrain`

Current phase execution policy:
- `retrain_dry_run` is runtime-active, emits a strict canonical preview payload when structurally possible, and marks it approval-ready only when every safety input passes;
- `POST /api/model-redesign/approval` creates an Admin-authenticated, actor/hash-bound pending record through the private Supabase ledger RPC;
- `GET /api/model-redesign/approval/[approval_id]` reads the authoritative record after server-side expiration materialization;
- `POST /api/model-redesign/approval/[approval_id]/decision` performs CAS-versioned independent approval/rejection or requester revocation;
- `POST /api/model-redesign/approval/assess` recomputes the payload hash and every submission precondition for Admin callers;
- approval persistence and assessment always return execution disabled and cannot write an artifact, start a job, or switch the active model;
- the migration is repository-ready but remains unapplied until the isolated Staging migration gate is explicitly approved;
- approved job submission is implemented as an idempotent durable ledger RPC; it queues work but does not dispatch a worker from the web request.

Response envelope (all endpoints):
- `success`: boolean
- `state`: `pass|warn|fail`
- `code`: stable reason code
- `error`: sanitized message only

## 7. UI Design (Future Navigation Freeze)

Workbench future flow stages:
1. dry-run preview
2. approval request preview
3. approval status
4. approved job submit
5. job status
6. result comparison
7. active model switch request

Current phase constraints:
- the Admin workbench can create/read/decide an approval, queue one approved job, and refresh its status;
- the web request never dispatches the worker or switches the active model;
- result comparison, promotion, active-model switch, and retirement remain separate, unimplemented approval boundaries.

## 8. Security and Safety Constraints

- reject path-like inputs (`filePath`, `reportPath`, `modelPath`, `path`, `sourcePath`)
- no `service_role` key usage in frontend routes
- no secret/token/env value in response or logs
- no mutation of `.active_model.json` in preview/approval phase
- no `.joblib` create/overwrite in preview/approval phase
- no production/base table write enablement

## 9. Implemented entry contract

Before implementing actual retrain, the repository now enforces:
- exact dry-run and approval-record schemas with unknown-field rejection;
- deterministic SHA-256 binding for the normalized feature contract and full dry-run payload;
- non-overlapping out-of-time periods, canonical future-field checks, immutable data-snapshot digest, active-model identity, deployed code version, and exact commit binding;
- separate requester/approver identities, approval chronology, expiration, immutable-state comparisons, Admin role, and staging/sandbox artifact policy;
- active model switch remains separately approved and unimplemented.

Runtime still requires an applied and runtime-verified Staging approval ledger, atomic job state machine, isolated artifact store, real out-of-time evaluation, and separate promotion approval. `MODEL_RETRAIN_ARTIFACT_WRITE_POLICY` defaults to `disabled`; changing it only affects eligibility assessment and does not enable a writer.

The pre-existing direct `/api/models/{model_id}/activate` path cannot serve as a bypass. Both proxy and FastAPI now reject it in Staging, Production, and unknown environments. Compatibility is available only when `APP_ENV` is local/test and `MODEL_ACTIVATION_LOCAL_ENABLED=true`; the default is false and the workbench does not set it.

The pre-existing synchronous `/api/train` and asynchronous `/api/train/start` artifact writers also cannot serve as an approval bypass. The Next proxy rejects before forwarding, FastAPI rejects before allocating a job and again at the write-capable training boundary, and the normal `/train` UI action is disabled. Compatibility requires local/test `APP_ENV` plus exact `MODEL_TRAINING_LOCAL_ENABLED=true`; deployed and unknown environments reject even when that flag is set.

Direct `DELETE /api/models/{model_id}` cannot bypass artifact lifecycle governance either. Next and FastAPI reject deletion in deployed and unknown environments before local or Supabase mutation, and `/train` does not offer an enabled delete action. Compatibility requires local/test `APP_ENV` plus exact `MODEL_DELETION_LOCAL_ENABLED=true`; a durable retirement approval is a separate future contract from retrain and promotion approval.

## 10. Implemented repository boundary

Contract implementation:
- `src/lib/model-retrain-approval-types.ts`
- `src/lib/model-retrain-approval-contract.ts`
- `src/lib/model-retrain-approval-ledger.ts`
- `src/app/api/model-redesign/approval/route.ts`
- `src/app/api/model-redesign/approval/[approval_id]/route.ts`
- `src/app/api/model-redesign/approval/[approval_id]/decision/route.ts`
- `src/app/api/model-redesign/approval/assess/route.ts`
- `supabase/migrations/20260802_model_retrain_approval_ledger.sql`
- `src/lib/model-retrain-job-ledger.ts`
- `src/app/api/model-redesign/jobs/route.ts`
- `src/app/api/model-redesign/jobs/[job_id]/route.ts`
- `src/components/ModelRetrainApprovalPanel.tsx`
- `supabase/migrations/20260802_model_retrain_job_ledger.sql`
- `supabase/migrations/20260802_model_retrain_worker_lease.sql`
- `supabase/migrations/20260802_model_retrain_artifact_registration.sql`
- `supabase/migrations/20260802_model_retrain_evaluation_registration.sql`
- `supabase/migrations/20260802_model_retrain_execution_bundle.sql`
- `supabase/migrations/20260802_model_retrain_orphan_reconciliation.sql`
- `python-api/training/approved_execution.py`
- `python-api/training/execution_bundle.py`
- `python-api/training/retrain_worker.py`
- `python-api/retrain_worker_main.py`
- `python-api/training/retrain_reconciler.py`
- `python-api/retrain_reconciler_main.py`
- `docs/model-retrain-worker-runbook.md`

Coverage:
- dry-run payload / preview contract
- approval record contract
- approved retrain job preconditions / submit request / result
- active model switch approval record boundary
- Admin workbench request/read/decision/queue/status controls without worker dispatch

Runtime policy:
- payload generation and eligibility assessment do not execute jobs.
- approval creation, transition, and queued-job submission are durable only after both migrations are explicitly applied and verified in isolated Staging;
- the approval/job ledgers are private, append-audited, CAS-bound, two-person, expiring, and approval-idempotent; only an approved requester can atomically change `job_created` from false to true while `execution_enabled` remains false;
- queued jobs can be atomically claimed with a 30-300 second lease, monotonic fencing token, CAS version, approval recheck, heartbeat, fenced start/failure reporting, and expired-lease recovery;
- expired claimed work returns to `queued`, while an expired running attempt becomes terminal `failed` to prevent unsafe duplicate execution;
- before registration, artifact fields remain structurally fixed to `artifact_written=false` and null identity;
- only the live fenced `running` worker can bind one existing object from the private `models` bucket. The object name is derived from the job UUID and SHA-256, size and media type are bounded, and the immutable registration moves the job to `artifact-registered`;
- artifact registration does not attest object contents, evaluate model quality, populate the active-model registry, or authorize activation/deletion;
- a service-only evaluator may move `artifact-registered` to `evaluation-recorded` only with the exact sanitized accepted-report schema, approved contract projection, matching candidate commit/artifact digest, all verifier checks true, empty blockers/failures, and a seven-day freshness bound;
- evaluation rows remain immutable with `trusted_promotion_evidence=false` and `promotion_eligible=false`; database registration cannot substitute for the signed Phase 3N artifact or activate a model;
- the one-shot coordinator maintains the lease during snapshot copy, training and upload, validates the execution bundle independently, rehashes the copied snapshot, uploads a digest-named artifact without upsert, registers under the current fence, and removes the object on handled pre-registration failure;
- the separate one-shot reconciler first recovers expired claimed/running leases through the existing CAS RPC, then considers only hour-old exact-name objects whose jobs are terminal `failed` with no artifact identity or immutable registration; registered, queued, claimed, running, malformed, and too-new objects are never candidates;
- each bounded reconciliation records deleted, not-found, delete-failed, or candidate-zero observations in an immutable service-only ledger and fails closed after auditing any incomplete deletion;
- the database contracts and local coordinator/trainer/uploader code exist, but no hosted migration application, deployed scheduler/worker execution, trusted evaluator, or switch runtime has been evidenced.
