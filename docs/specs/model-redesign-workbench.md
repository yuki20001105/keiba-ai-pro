# Model Redesign Workbench Specification

Updated: 2026-07-06

## 1. Scope

Purpose:
- Provide a guarded UI workflow for model redesign and improvement proposals.
- Replace script-only decision making with reviewable steps: proposal -> approval -> retrain.

Out of scope:
- Direct production/base table writes.
- Automatic model activation after training without explicit approval.
- Arbitrary command execution from UI.

Target users:
- Admin only for approval and execution.
- Premium may view reports if explicitly enabled later (default: Admin only).

## 2. Screen Specification

Route:
- `/model-redesign-workbench` (new, Admin guarded)

Screen sections:
- Proposal source panel:
  - select iteration metric file (`docs/reports/iter_*_metrics.json`) from allowlist
  - show recommendation summary and affected features
- Candidate change set panel:
  - proposed add/remove feature list
  - expected effect (AUC/logloss/feature_count diff)
  - risk tags (leakage risk, high-correlation risk, coverage risk)
- Approval panel:
  - approve/reject with reason (mandatory text)
  - immutable audit entry preview
- Retrain execution panel:
  - dry-run check
  - execute retrain job
  - progress and status timeline
- Promotion guard panel:
  - compare candidate model vs current active model
  - explicit activation control with safety checklist

UI states:
- `pass | warn | fail` status badges across each stage.
- Hard block on missing approval for retrain.
- Hard block on failed safety checks for activation.

## 3. API Specification

Base policy:
- Next API route layer performs Bearer auth + role check.
- FastAPI performs execution and model operations.
- No endpoint accepts arbitrary filesystem paths.

Proposed Next routes:
- `GET /api/model-redesign/proposals`
  - returns allowlisted proposal candidates
  - source limited to `docs/reports/iter_*_metrics.json`
- `POST /api/model-redesign/proposals/preview`
  - input: `proposal_id`
  - output: normalized proposal summary
- `POST /api/model-redesign/proposals/approve`
  - input: `proposal_id`, `decision=approve|reject`, `reason`
  - output: approval record id
- `POST /api/model-redesign/jobs/start`
  - input: `approval_id`, `target=win`, `mode=dry-run|train`
  - output: `job_id`
- `GET /api/model-redesign/jobs/[job_id]`
  - output: progress, step status, artifacts
- `POST /api/model-redesign/promote`
  - input: `approval_id`, `candidate_model_id`, `activate=true`
  - output: activation result (or guard failure)

Response contract:
- `success` boolean
- `state` (`pass|warn|fail`)
- `code` stable reason code
- `error` sanitized message only

## 4. Job Management

Execution stages:
1. Validate approval and role
2. Build training config from approved proposal
3. Run leakage guard precheck
4. Run training
5. Evaluate candidate metrics
6. Persist artifacts and metadata
7. Emit promote-eligible status

Job state machine:
- `queued -> running -> succeeded | failed | canceled`

Required job fields:
- `job_id`
- `approval_id`
- `requested_by`
- `started_at`, `finished_at`
- `state`
- `step_results`
- `artifact_refs` (model id, metrics path)

Audit requirement:
- every state transition is recorded with actor + timestamp.

## 5. Proposal -> Approval -> Retrain Flow

Flow:
1. User opens proposal preview (read-only).
2. Admin approves or rejects with mandatory reason.
3. Approved proposal can start dry-run.
4. Dry-run must be `pass` before train execution.
5. Train job creates candidate model + metrics.
6. Candidate is compared with active model.
7. Promotion requires explicit Admin confirmation.

Guard conditions:
- no approval: retrain blocked.
- failed leakage check: retrain blocked.
- candidate underperforms threshold: promote blocked (warn/fail by policy).

## 6. Production Reflection Guard

Hard guards:
- no production/base table write in workbench flow.
- no auto-activation of active model on train completion.
- no direct mutation of `.active_model.json` from client input.
- activation requires server-side revalidation of:
  - model existence
  - feature contract compatibility
  - required evaluation artifacts

Promotion preconditions:
- approved proposal id exists
- candidate model evaluation exists
- leakage check state is pass
- manual confirmation flag is true

## 7. Security and Secrets

- no service_role key usage from frontend.
- no secret value in response payload.
- sanitize logs and errors.
- reject path-like inputs (`path`, `filePath`, `reportPath`, `sourcePath`).

## 8. Smoke / E2E Acceptance

Minimum checks:
- non-admin receives 403 on approve/start/promote.
- unapproved proposal cannot start train job.
- failed dry-run cannot transition to train.
- promote endpoint rejects incompatible candidate.
- activation path records audit event.

## 9. Rollout Plan

Phase 1 (spec + API skeleton):
- add routes with mocked execution and full guards.

Phase 2 (job runtime integration):
- connect to optimizer/retrain pipeline.

Phase 3 (promotion integration):
- guarded active-model activation and smoke coverage.
