# Limited Production Observation Release

## Decision

System release readiness and model business validation are separate gates.
The existing full release remains unchanged: an `ACTIVE` model still requires
the approved model-acceptance contract. A limited release may run in Production
with `MODEL_RUNTIME_STATUS=observation` only to serve predictions and collect
prospective evidence.

The canonical policy is
`config/limited_production_observation_contract.v1.json`. Its durable approval
reference is PR #28; the policy is not externally effective until that exact
tree is reviewed and merged.

## State model

| State | Predictions | Prospective observations | Automatic betting | Business-validated claim |
|---|---:|---:|---:|---:|
| `research` | local only | no | no | no |
| `observation` | yes | yes | **no** | **no** |
| `validated` | yes | yes | no | yes |
| `active` | yes | yes | separately approved only | yes |

Passing the limited release gate produces
`system_release_ready=true` and `observation_release_ready=true`, while keeping
`model_business_validated=false`, `full_production_ready=false`, and
`automated_betting_allowed=false`.

## Required Production configuration

Start from `.env.production.template`. The following values are mandatory for
observation capture; secrets and provider-specific values remain in the
provider secret store.

```text
APP_ENV=production
MODEL_RUNTIME_STATUS=observation
AUTOMATED_BETTING_ENABLED=false
PHASE3N_OBSERVATION_RELEASE_MODE=limited-observation
PHASE3N_RELEASE_CONTRACT_ID=limited-production-observation-v1
PHASE3N_PRODUCTION_PROJECT_REF=<production-project-ref>
PHASE3N_CANDIDATE_COMMIT_SHA=<exact-full-sha>
PHASE3N_EXPANDING_WINDOW_CHECKS_PASSED=true
PHASE3N_OBSERVATION_ENABLED=true
```

Enable `PHASE3N_OBSERVATION_ENABLED` last. The runtime rejects a mismatched
Supabase host/project, non-exact SHA, missing expanding-window assertion,
non-observation model status, unknown contract, or automatic-betting opt-in.
The health endpoint exposes only non-secret mode booleans for smoke checks.

## Release sequence

1. Merge the reviewed candidate from `develop` to `main` without changing its tree.
2. Update and independently review the immutable trusted-producer branch.
3. Run `staging-evidence.yml` with `evidence_scope=limited-observation` for the exact candidate.
4. Configure repository variable `PRODUCTION_RELEASE_MODE=limited-observation` and bind the resulting run ID.
5. Obtain the protected `production-release` Environment approval.
6. Run `release.yml` with `release_mode=limited-observation`; this authorizes but does not deploy provider resources.
7. Configure the Production providers with the fail-closed values above and deploy the exact authorized merge.
8. Verify `/health`, authenticated prediction, append-only observation, monitoring, and rollback without enabling betting.

## Monitoring and alert minimums

- Render and Vercel health checks must alert on consecutive non-2xx responses.
- Supabase connectivity and observation RPC failures must alert without logging secrets or row payloads.
- Scrape/odds freshness failures, prediction failures, model-load failures, and observation-write failures need separate counters.
- The smoke check must verify the exact SHA, `MODEL_RUNTIME_STATUS=observation`, observation enabled, and automatic betting disabled.
- Incident ownership and escalation timing must be recorded in the protected release approval/runbook.

## Rollback

1. Set `PHASE3N_OBSERVATION_ENABLED=false` to stop new evidence writes.
2. Roll the application back to the previously authorized exact commit.
3. Keep append-only prediction/result records; do not delete or rewrite evidence.
4. Do not reverse append-only database migrations. Use a reviewed compensating migration if schema repair is required.
5. Confirm health/auth, model status, automated-betting-disabled state, and database connectivity after rollback.

## Deferred model validation

The following remain mandatory before `validated` or `active`, but are no
longer prerequisites for the limited observation release:

- at least 90 settled observation days;
- at least 1,000 settled samples and 100 qualifying virtual bets;
- AUC >= 0.85, ROI >= 3%, maximum drawdown <= 20%, and baseline delta >= 1 point;
- Brier <= 0.20, ECE <= 0.05, P95 latency <= 500 ms, and source freshness <= 30 minutes;
- trusted model-evaluation evidence for the exact artifact, features, policy, source, and commit.

Automatic betting remains outside this deferral. It is denied unless a future
reviewed release explicitly moves the model to `active` and independently opts
in to the betting control.
