# Model Acceptance Contract

## Purpose

`config/model_acceptance_contract.v1.json` is the versioned business acceptance contract for a model promotion candidate. It separates model quality observed by code from thresholds approved by the business owner.

The repository currently has one authoritative threshold: AUC must be at least 0.85 (`docs/specs/SYSTEM.md`). The other values remain `null` and the contract remains `draft` until an authorized owner approves them. A draft contract can be validated, but it can never produce `accepted=true`.

## Approval decisions still required

| Metric | Direction | Current value | Required decision |
|---|---|---:|---|
| AUC | at least | 0.85 | Already defined by SYSTEM.md |
| Brier score | at most | unapproved | Maximum acceptable probability error |
| Expected calibration error | at most | unapproved | Maximum calibration-bin error |
| Out-of-time ROI | at least | unapproved | Net ROI after the agreed staking/cost policy |
| Maximum drawdown | at most | unapproved | Maximum peak-to-trough bankroll loss |
| Bet count | at least | unapproved | Minimum number of qualifying bets |
| Evaluation sample count | at least | unapproved | Minimum holdout population |
| P95 prediction latency | at most | unapproved | Service/model latency budget |
| Data freshness | at most | unapproved | Maximum age of source data at prediction time |
| Observation period | at least | unapproved | Minimum calendar coverage of the holdout |
| ROI delta to baseline | at least | unapproved | Required advantage over the approved baseline |

Approval requires all threshold values plus `approved_at`, `approved_by`, and `approval_reference`. The reference must identify a durable review record; a chat statement or local edit is not sufficient evidence.

## Evidence contract

The verifier at `scripts/verify_model_acceptance.py` requires evidence bound to:

- the exact 40-character candidate commit SHA;
- the exact contract ID and canonical SHA-256 digest;
- one bounded model ID;
- a fresh observation timestamp and a valid evaluation window;
- an out-of-time holdout with no future-field leakage detected;
- every required metric, using finite JSON numbers and integer sample/bet counts.

Unknown or missing fields, duplicate JSON keys, non-finite numbers, stale evidence, mismatched commits/contracts, and malformed timestamps fail closed. Output is sanitized to IDs, timestamps, status, checks, blockers, and failure codes; raw feature or customer data is not copied into the gate report.

Example assessment command:

```powershell
python-api/.venv/Scripts/python.exe scripts/verify_model_acceptance.py `
  --contract config/model_acceptance_contract.v1.json `
  --evidence path/to/model_acceptance_evidence.json `
  --expected-commit (git rev-parse HEAD)
```

For a promotion gate, add `--require-accepted`. That mode exits nonzero unless the approved contract and all bound evidence pass.

## Promotion boundary

This contract does not make the current model Production-ready. The approval record and current-commit out-of-time evidence do not yet exist. Until both are supplied by a trusted workflow, Production remains `NOT_READY` even if repository tests and isolated Staging operational checks pass.
