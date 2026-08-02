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

## Evidence construction and contract

`scripts/build_model_acceptance_evidence.py` is the only trusted-workflow path from row observations to aggregate acceptance evidence. It recomputes every metric instead of accepting caller-supplied totals. Its strict `model-evaluation-observations` version 1 input contains:

- the exact candidate commit, model ID, and model-artifact SHA-256;
- the generation timestamp, training-data cutoff, and `out_of_time` policy;
- the exact ordered model feature-column list and a passing expanding-window check;
- initial bankroll and bounded row observations;
- for each row, a unique ID, JST race date, prediction/data/settlement timestamps, binary outcome, probability, candidate and baseline wager/return values, and latency.

Every prediction must occur after the training cutoff. Source data must exist before prediction, settlement must follow prediction and precede evidence generation, and the declared race date must match the prediction date in `Asia/Tokyo`. The model feature list is intersected with the canonical `keiba_ai.constants.FUTURE_FIELDS` blocklist. Missing classes, candidate or baseline wagers, duplicate IDs/columns, non-finite values, unknown fields, and invalid financial relationships fail closed.

The builder deterministically calculates AUC with average ranks for ties, Brier score, 10-bin equal-width ECE, candidate ROI, settlement-grouped maximum bankroll drawdown, bet/sample count, nearest-rank P95 latency, worst data freshness, inclusive race-date coverage, and ROI delta to the baseline. The evidence binds the model artifact, ordered feature columns, and canonical source observations by separate SHA-256 digests.

The verifier at `scripts/verify_model_acceptance.py` then requires that evidence to be bound to:

- the exact 40-character candidate commit SHA;
- the exact contract ID and canonical SHA-256 digest;
- one bounded model ID plus model-artifact, feature-column, and source-observation digests;
- a fresh observation timestamp and a valid evaluation window;
- an out-of-time holdout with no future-field leakage detected;
- every required metric, using finite JSON numbers and integer sample/bet counts.

Unknown or missing fields, duplicate JSON keys, non-finite numbers, stale evidence, mismatched commits/contracts, and malformed timestamps fail closed. Output is sanitized to IDs, timestamps, status, checks, blockers, and failure codes; raw feature or customer data is not copied into the gate report.

Example assessment command:

```powershell
python-api/.venv/Scripts/python.exe scripts/build_model_acceptance_evidence.py `
  --input path/to/model_evaluation_observations.json `
  --contract config/model_acceptance_contract.v1.json `
  --expected-commit (git rev-parse HEAD) `
  --output reports/model_acceptance_evidence.json

python-api/.venv/Scripts/python.exe scripts/verify_model_acceptance.py `
  --contract config/model_acceptance_contract.v1.json `
  --evidence reports/model_acceptance_evidence.json `
  --expected-commit (git rev-parse HEAD)
```

For a promotion gate, add `--require-accepted`. That mode exits nonzero unless the approved contract and all bound evidence pass.

An accepted sanitized report may be persisted by the service-only
`register_model_retrain_accepted_evaluation` RPC after the candidate artifact is
immutably registered. The database rechecks the report schema, accepted verdict,
approved contract projection, exact candidate commit and artifact digest, all five
boolean checks, empty blockers/failures, and freshness. This record is an evaluation
handoff only: `trusted_promotion_evidence` and `promotion_eligible` are structurally
false. Only the separately attested Phase 3N workflow may authorize promotion.

## Promotion boundary

The trusted workflow receives gzip-compressed, base64-encoded row observations through the protected `MODEL_EVALUATION_OBSERVATIONS_GZIP_B64` Environment value. It bounds decompression, rebuilds the aggregate evidence, deletes both raw and aggregate inputs after verification, and retains only the sanitized gate report. This protects the gate from hand-edited aggregate metrics; the reviewed source observation set must still be retained in the approved external evidence system under the emitted digest.

This contract does not make the current model Production-ready. The approval record and current-commit out-of-time observations do not yet exist. Until both are supplied by a trusted workflow, Production remains `NOT_READY` even if repository tests and isolated Staging operational checks pass.
