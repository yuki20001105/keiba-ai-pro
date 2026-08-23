# Model Redesign Workbench Design Notes

Updated: 2026-08-02
Scope: design extension for approval boundary freeze

## 1. Relationship to Existing Spec

Primary specification remains:
- `docs/specs/model-redesign-workbench.md`

This document adds phase-gated design notes for:
- retrain dry-run payload schema freeze
- approval record schema freeze
- execution preconditions before actual retrain

## 2. Current Implemented Stage

Implemented now:
- read-only workbench summary
- `action=retrain_dry_run` preview
- strict approval payload and canonical SHA-256 generation
- Admin-only `/api/model-redesign/approval/assess` precondition assessment
- smoke/E2E coverage for preview safety

Not implemented now:
- durable approval creation/storage
- actual retrain
- approved job submit runtime
- active model pointer switch runtime

## 3. Contract Freeze (This Stage)

Frozen payload contract:
- `ModelRetrainDryRunPayload`
- fields include target/model/period/features/expected outputs/runtime/safety
- traceability fields include active/source model, feature hash, data snapshot, git commit

Frozen approval contract:
- `ModelRetrainApprovalRecord`
- approval identity, approver metadata, payload hash, expiration/invalidation, allowed actions

Detailed schema source:
- `docs/model-retrain-approval-design.md`

## 4. Execution Boundary

Execution is still blocked in this stage.
The following remain hard-blocked:
- actual retrain runtime
- `.joblib` artifact persistence
- `.active_model.json` mutation
- active model pointer switch
- production/base table write

The assessment route rechecks payload, approval, expiry, requester/approver separation, active model, feature contract, code version, commit, role, and artifact scope. It always returns `execution_performed=false`; an eligible assessment is not execution authority.

The legacy model activation proxy/backend is fail-closed in every deployed or unknown environment and is not a promotion mechanism. It exists only for explicit local/test compatibility; the normal `/train` UI no longer offers direct pointer mutation.

## 5. Future API and UI Surface

Defined for next phase only (not runtime-active now):
- `POST /api/model-redesign/approval` (`action=create_approval`)
- `GET /api/model-redesign/approval/:approval_id`
- `POST /api/model-redesign/job` (`action=submit_approved_retrain`)

Future UI lanes:
- approval request preview
- approval status
- approved job submit
- job status
- result comparison
- active model switch request

## 6. Approval Separation Rule

Retrain approval and active model switch approval are separated.
Even after approved retrain execution, active model pointer change must require separate Admin approval.
