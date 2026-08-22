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

`.github/workflows/limited-production-monitor.yml` implements the free monitoring
path. Every enabled run probes the frontend and backend safety envelope,
Supabase Auth, the operational RPC, recent rejected/conflicting observation
ingest attempts, and the Render provider-bound live commit. Failures are posted
to incident Issue #32 without secrets or row payloads. Before enabling the
schedule, configure the four `PRODUCTION_*_URL/SERVICE_ID` repository variables,
the existing Production `SUPABASE_SERVICE_KEY` and a `RENDER_API_KEY` repository secret,
run the non-destructive notification test, and then set
`LIMITED_PRODUCTION_MONITOR_ENABLED=true`.
- Incident ownership and escalation timing must be recorded in the protected release approval/runbook.

## Operational ownership and fail-closed preflight

The Primary incident owner is `yuki20001105`. The durable escalation route is
[GitHub issue #32](https://github.com/yuki20001105/keiba-ai-pro/issues/32),
which was created and assigned as a non-destructive notification-path test on
2026-08-16. The owner must acknowledge a release-blocking alert immediately,
disable observation writes within five minutes, and make a rollback decision
within fifteen minutes. If the owner is unavailable, no Production deploy or
continued observation write is authorized.

Any of the following requires immediate observation shutdown and rollback
assessment:

- an observation write failure, duplicate, stale write, or exact-SHA mismatch;
- unexpected model activation/training or a runtime state other than
  `observation`;
- automatic betting enabled or any live purchase attempt;
- Supabase identity/connectivity mismatch or a database integrity anomaly;
- repeated non-2xx health/API responses or an Auth/RLS boundary regression.

The 2026-08-16 read-only preflight is recorded in
`reports/limited_production_operational_preflight_20260816.json`. It remains
fail-closed. Authenticated Vercel review identified Production project
`keiba-ai-pro` (`prj_UcRc...`) separately from trusted Staging project
`keiba-ai-pro-staging` (`prj_PRd5...`). Production currently serves provider
deployment `6sU5g1...` from SHA `69dd9e9...`. The root returned 200 while an
initial `/api/health` request returned 503 after 4.01 seconds. The route has a
four-second backend timeout; after the Render backend was warm, three
consecutive checks returned 200 in 734-1,194 ms. The evidence therefore treats
the 503 as a cold-start-sensitive health design, not as proof of Supabase
failure, and monitoring remains open until that distinction is handled.
Native anomaly alerts are unavailable on the Hobby plan. The Production
project has only the five legacy application/Supabase variables, all scoped to
all environments, and lacks the exact-SHA, release-contract, observation-mode,
observation-enable, automatic-betting, training, and activation safety names.
The current Render workspace still manages only the Staging backend and no
provider-bound healthy Production rollback SHA exists. Repository `main` at
`62748ce...` is not a substitute for a verified deployed rollback target.

After explicit approval, Production Supabase `grfwkutcsavqicaimssn` was
resumed without a database write. Auth health, REST schema access, and a bounded
Auth Admin read all returned 200, and the service-role claim bound the expected
project ref. The dashboard migration history is empty and the REST OpenAPI
contains only 29 legacy paths; none of the seven Phase3N observation/HA tables
is present. Connectivity and identity therefore pass, but the Production
schema/migration gate fails closed. Applying migrations requires a separate
explicit Production migration approval and is not covered by the resume
authorization.

The legacy adoption review is specified in
`docs/phase3n_legacy_production_schema_adoption.md`. Its standalone preflight
is repeatable-read/read-only. The complete adoption review SQL archives source
tables, rebuilds the canonical 21-migration public schema, and copies reviewed
rows, but currently contains an unconditional pre-change abort because
`migration_apply_authorized=false`. Production has not been migrated. The
data-bearing Preview validation, forced rollback probe, exact 21-migration
apply, and adoption-adjusted Auth/RLS/IDOR contract now pass. A completed
Production physical backup is recorded, the Preview was deleted, and the
temporary CLI token was revoked. These results remove the clone and restore-
point blockers but do not grant Production migration authority; the separate
approval and maintenance-window gates remain mandatory.
The review contract must still be approved separately before an apply-capable
Production bundle may be rendered.

## Rollback

1. Set `PHASE3N_OBSERVATION_ENABLED=false` to stop new evidence writes.
2. Roll the application back to the previously authorized exact commit.
3. Keep append-only prediction/result records; do not delete or rewrite evidence.
4. Do not reverse append-only database migrations. Use a reviewed compensating migration if schema repair is required.
5. Confirm health/auth, model status, automated-betting-disabled state, and database connectivity after rollback.

Before deployment, record a provider-bound `known_good_production_sha` that is
distinct from candidate `86a2d314a641160e852d3597396aadcd03e81347`. A rollback
is incomplete until `/health`, Auth, the expected Supabase project identity,
`MODEL_RUNTIME_STATUS=observation`, observation-only behavior, and
`AUTOMATED_BETTING_ENABLED=false` all pass on that exact rollback SHA. If no
verified prior SHA exists, the fail-closed rollback is to disable observation
writes and stop the release rather than claim that the repository `main` SHA
is a working provider rollback target.

## Credential rotation evidence

On 2026-08-16 the current Staging E2E password authenticated successfully. Two
superseded Staging Supabase Secret keys (`default` and
`render_staging_f2614e4_v2`) were removed after confirming that Render used the
separate remaining key; the removed credentials then returned HTTP 401 while
the remaining Render key returned HTTP 200 in a server context. Secret scanning
reported zero tracked/staged candidates. Authenticated Vercel review found no
E2E credential variables and confirmed that its legacy Production service-role
credential is distinct in format and value from the current/retired Staging
Secret keys. It is not marked Sensitive and is scoped to all environments, so
that hardening remains open. The credential gate is also open until the prior
E2E password is shown to fail. No credential value belongs in this document or
its evidence JSON.

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
