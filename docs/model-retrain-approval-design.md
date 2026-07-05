# Model Retrain Approval Design

Updated: 2026-07-06
Status: design freeze (no runtime execution in this phase)

## 1. Purpose and Non-goals

Purpose:
- Fix the approval target boundary before implementing actual retrain jobs.
- Define immutable contracts for dry-run payload and approval record.
- Define what becomes executable only after approval.

Non-goals in this phase:
- No actual retrain execution.
- No `.joblib` create/overwrite.
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

## 6. API Design (Specification-only in this phase)

In-scope API contracts:
- `POST /api/model-redesign/summary` with `action=retrain_dry_run`
- `POST /api/model-redesign/approval` with `action=create_approval`
- `GET /api/model-redesign/approval/:approval_id`
- `POST /api/model-redesign/job` with `action=submit_approved_retrain`

Current phase execution policy:
- only `retrain_dry_run` is runtime-active.
- approval/job endpoints are design placeholders for next phase.

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
- actual submit buttons remain `disabled/not-implemented`.
- no action executes actual retrain or pointer switch.

## 8. Security and Safety Constraints

- reject path-like inputs (`filePath`, `reportPath`, `modelPath`, `path`, `sourcePath`)
- no `service_role` key usage in frontend routes
- no secret/token/env value in response or logs
- no mutation of `.active_model.json` in preview/approval phase
- no `.joblib` create/overwrite in preview/approval phase
- no production/base table write enablement

## 9. Next Phase Entry Criteria

Before implementing actual retrain:
- dry-run payload schema is versioned and fixed.
- approval record schema is versioned and fixed.
- hash canonicalization is implemented and tested.
- approval expiration and invalidation rules are enforced.
- active model switch remains separately approved.