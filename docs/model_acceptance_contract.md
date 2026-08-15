# Model Acceptance Contract

## Purpose

`config/model_acceptance_contract.v1.json` is the versioned business acceptance contract for a model promotion candidate. It separates model quality observed by code from thresholds approved by the business owner.

The authorized owner approved the complete Phase 3N threshold set in [GitHub issue #25](https://github.com/yuki20001105/keiba-ai-pro/issues/25#issuecomment-5157389848) on 2026-08-02. The canonical contract is now `approved`; it can produce `accepted=true` only when fresh current-commit out-of-time observations satisfy every threshold and verifier check.

The same owner approved the exact `phase3n-tansho-flat-v1` candidate/baseline
staking and payout policy in [GitHub issue #29](https://github.com/yuki20001105/keiba-ai-pro/issues/29#issuecomment-5294613277)
on 2026-08-14. The policy status and durable reference are part of the canonical
policy digest; any later change requires a new review reference.

## Approved thresholds

| Metric | Direction | Approved value | Meaning |
|---|---|---:|---|
| AUC | at least | 0.85 | Existing authoritative SYSTEM.md requirement |
| Brier score | at most | 0.20 | Maximum acceptable probability error |
| Expected calibration error | at most | 0.05 | Maximum calibration-bin error |
| Out-of-time ROI | at least | 3.0% | Net ROI after the agreed staking/cost policy |
| Maximum drawdown | at most | 20.0% | Maximum peak-to-trough bankroll loss |
| Bet count | at least | 100 | Minimum number of qualifying bets |
| Evaluation sample count | at least | 1,000 | Minimum holdout population |
| P95 prediction latency | at most | 500 ms | Service/model latency budget |
| Data freshness | at most | 30 minutes | Maximum age of source data at prediction time |
| Observation period | at least | 90 days | Minimum calendar coverage of the holdout |
| ROI delta to baseline | at least | 1.0 percentage point | Required advantage over the approved baseline |

Approval requires all threshold values plus `approved_at`, `approved_by`, and `approval_reference`. The reference must identify a durable review record; a chat statement or local edit is not sufficient evidence.

## Evidence construction and contract

`scripts/build_model_acceptance_evidence.py` is the only trusted-workflow path from row observations to aggregate acceptance evidence. It recomputes every metric instead of accepting caller-supplied totals. Its strict `model-evaluation-observations` version 2 input contains:

- the exact candidate commit, model ID, and model-artifact SHA-256;
- the generation timestamp, training-data cutoff, and `out_of_time` policy;
- the exact ordered model feature-column list and a passing expanding-window check;
- initial bankroll and bounded row observations;
- the exact approved staking/payout policy ID, canonical SHA-256 and durable
  GitHub approval reference;
- for each row, a unique ID, JST race date, prediction/data/settlement timestamps, binary outcome, probability, candidate and baseline wager/return values, and latency.

Every prediction must occur after the training cutoff. Source data must exist before prediction, settlement must follow prediction and precede evidence generation, and the declared race date must match the prediction date in `Asia/Tokyo`. The model feature list is intersected with the canonical `keiba_ai.constants.FUTURE_FIELDS` blocklist. Missing classes, candidate or baseline wagers, duplicate IDs/columns, non-finite values, unknown fields, and invalid financial relationships fail closed.

The builder first requires the tracked
`config/phase3n_staking_payout_policy.v1.json` to be `approved`, recomputes its
digest, and matches its ID, digest and approval reference to the observation
source. It then deterministically calculates AUC with average ranks for ties,
Brier score, 10-bin equal-width ECE, candidate ROI, settlement-grouped maximum
bankroll drawdown, bet/sample count, nearest-rank P95 latency, worst data
freshness, inclusive race-date coverage, and ROI delta to the baseline. The
evidence binds the model artifact, ordered feature columns, policy-bound source
observations and contract by separate SHA-256 digests.

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

## Source-readiness preflight

Before constructing row observations, audit a candidate SQLite source without exporting row data:

```powershell
python-api/.venv/Scripts/python.exe scripts/audit_model_acceptance_source.py `
  --database C:/absolute/read-only/source.db `
  --model-id <bounded-model-id> `
  --training-cutoff YYYY-MM-DD
```

The command opens only an absolute, non-symlink SQLite file with `mode=ro`, `immutable=1`, and `query_only=ON`. Its sanitized JSON contains aggregate counts, capability booleans and blocker codes; it contains no database path, horse identity or source row. Exit `0` means the selected rows contain the minimum strict source fields, exit `2` is a valid but non-ready audit, and exit `1` is invalid configuration/schema/query failure. Passing this audit is only collection preflight: the trusted builder still validates every value, digest, temporal relationship, feature column and financial relationship.

The 2026-08-02 read-only parent-worktree audit for active-model suffix `20260418_1928` and training cutoff `2026-03-22` found 1,024 prediction rows, 1,014 same-day post-cutoff rows, and 15 settled labels (one win and fourteen losses). It found no complete timezone-aware prediction timestamp, data-observed timestamp, settlement timestamp, candidate wager/return, baseline wager/return, or latency capability. This local aggregate is not trusted model evidence and supplies no threshold authority. It proves that the existing database must not be copied or repackaged as Phase 3N input; a controlled Staging evaluator must collect fresh strict rows under an approved staking and baseline policy.

An accepted sanitized report may be persisted by the service-only
`register_model_retrain_accepted_evaluation` RPC after the candidate artifact is
immutably registered. The database rechecks the report schema, accepted verdict,
approved contract projection, exact candidate commit and artifact digest, all five
boolean checks, empty blockers/failures, and freshness. This record is an evaluation
handoff only: `trusted_promotion_evidence` and `promotion_eligible` are structurally
false. Only the separately attested Phase 3N workflow may authorize promotion.

The fail-closed one-shot runtime at `python-api/retrain_evaluator_main.py` connects this contract to that RPC in Staging/Sandbox. It reads strict observations from an absolute non-symlink path, rebuilds evidence without writing a raw/evidence output file, requires `--require-accepted`-equivalent verifier semantics, independently rechecks the sanitized report, and submits it with the exact job CAS version and evaluator identity. Its successful `evaluation-recorded` state is still non-promoting and is not trusted Phase 3N evidence.

## Promotion boundary

The trusted workflow receives gzip-compressed, base64-encoded row observations through the protected `MODEL_EVALUATION_OBSERVATIONS_GZIP_B64` Environment value. It bounds decompression, rebuilds the aggregate evidence, deletes both raw and aggregate inputs after verification, and retains only the sanitized gate report. This protects the gate from hand-edited aggregate metrics; the reviewed source observation set must still be retained in the approved external evidence system under the emitted digest.

This contract does not make the current model business-validated. The durable approval record now exists, but current-commit out-of-time observations do not. Until those observations pass the trusted workflow, the model must remain outside `validated` and `active` even if repository tests and isolated Staging operational checks pass. A separately authorized Limited Production observation release may run with `MODEL_RUNTIME_STATUS=observation`; it does not weaken or satisfy this contract and cannot enable automatic betting.
