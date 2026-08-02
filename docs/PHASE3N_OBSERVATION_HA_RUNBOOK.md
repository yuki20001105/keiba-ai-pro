# Phase 3N observation, cache integrity, and HA runbook

## Scope and current verdict

This extension collects immutable model observations in **Supabase Staging** and
tests stateless worker recovery. It does not connect to Production, modify the
first 19 migrations, create synthetic acceptance observations, or register the
two trusted-evidence B64 variables.

The current verdict remains **Production NOT_READY**. Implementation and
synthetic contract tests cannot replace 90 calendar days, 1,000 valid settled
samples, 100 qualifying bets, or trusted review of the resulting evidence.

## Architecture

The append-only migration chain now has two additions:

1. Ordinal 20 adopts the existing shared operational outbox and fencing
   migration into the canonical bootstrap manifest.
2. Ordinal 21 adds immutable prediction/result ledgers, ingest-attempt audit
   events, and shared HA job/effect/event tables.

Prediction records are written before the API response is returned. PostgreSQL
assigns `prediction_at` and `recorded_at` with `clock_timestamp()`. Each record
binds race, horse, model version, model artifact hash, candidate commit SHA,
feature-manifest hash, feature-row hash, data timestamps, probability, rank,
odds, recommendation, wager fields, latency, and environment. Results are later
appended as separate events; predictions are never updated with outcomes.

Only `service_role` can execute the SECURITY DEFINER RPCs or read these tables.
`anon` and `authenticated` receive no table or RPC access. Immutable tables
reject `UPDATE` and `DELETE`, including service-role calls. Secrets, card data,
access tokens, and raw credentials are not observation fields and must not be
logged.

Render instances own no durable state. Jobs, leases, fencing tokens, effects,
and observation rows live in PostgreSQL. A local cache is derived data only and
is rebuilt atomically from the canonical ledger.

## Staging activation boundary

Observation capture is disabled by default and fails closed when partially or
incorrectly configured. Enable it only after the exact candidate has passed CI,
the two new migrations have been append-only applied to the authorized Staging
project, and the expanding-window checks for that same commit are green.

Configure the Render Staging service with these names (do not put values in a
ticket, commit, or log):

```text
PHASE3N_OBSERVATION_ENABLED=true
APP_ENV=staging
PHASE3N_STAGING_PROJECT_REF=<authorized-staging-project-ref>
PHASE3N_CANDIDATE_COMMIT_SHA=<exact-deployed-40-character-sha>
PHASE3N_EXPANDING_WINDOW_CHECKS_PASSED=true
SUPABASE_URL=<authorized-staging-url>
SUPABASE_SERVICE_ROLE_KEY=<staging-only-secret>
```

The code verifies HTTPS, exact project-ref/URL agreement, an exact commit SHA,
and the expanding-window assertion. It rejects Production configuration.

Automatic prediction capture intentionally records `qualifying_bet=false` and
zero stake today. The current prediction response does not expose an approved
per-horse staking decision and payout policy, so manufacturing those values
would invalidate ROI evidence. An approved source-backed staking/payout adapter
is therefore a real remaining blocker for the 100-bet threshold.

## Result reconciliation and daily progress

The following commands use only the Staging project selected by the boundary
above:

```powershell
python scripts/phase3n_observation.py reconcile-results
python scripts/phase3n_observation.py progress
python scripts/phase3n_observation.py cache-rebuild
```

Reconciliation reads authoritative settled results and appends only non-bet
outcomes for now. It skips qualifying wagers until the approved payout mapping
exists. Repeating a command is safe: idempotency keys prevent a second logical
record, payload-binding conflicts fail closed, and attempts remain auditable.

`progress` writes JSON and Markdown under `reports/`. The report contains the
observation start/end, elapsed days, valid samples, qualifying bets, physical or
conflicting duplicates, safely rejected idempotent replays, missing results,
leakage violations, invalid timestamps, model/commit counts, each threshold,
and the fail-closed verdict.

Do not add an unreviewed GitHub schedule with a long-lived service-role secret.
Request-path prediction capture is automatic. Result reconciliation is ready
for an approved least-privilege Staging scheduler, but scheduling remains off
until its identity, environment protection, retry policy, and unlock behavior
are reviewed. Existing trusted-producer branch and GitHub Environment controls
must remain unchanged.

## Cache integrity test

The cache serializer sorts canonical rows, normalizes JSON, and hashes the
canonical payload. The isolated contract performs this sequence:

1. Seed canonical synthetic rows in disposable PostgreSQL.
2. Build a local cache and record its SHA-256 digest.
3. kill the cache-building container with SIGKILL.
4. delete the local cache.
5. rebuild it only from PostgreSQL.
6. require equal pre/post cache digests and zero missing, duplicate, or stale
   rows.
7. require the canonical observation-table digest to be unchanged.

The evidence is marked `synthetic_contract_test=true` and
`production_evidence=false`.

## Two-instance crash and fencing test

`docker-compose.phase3n-ha.yml` starts disposable PostgreSQL, PostgREST, and two
instances of the same worker image. No host port, external credential,
Persistent Disk, or Production connection is used. CI runs SIGKILL, SIGTERM,
and network-partition scenarios:

1. Instance A claims a lease and receives fencing token N.
2. A is interrupted while holding the lease.
3. After expiry, Instance B takes over with a token greater than N.
4. B commits the effect once.
5. A's stale token is rejected after recovery.
6. The final effect count is one; duplicate processing/bets/scheduler effects
   and split brain remain zero.
7. The event timeline, recovery times, fencing history, cache digests, and
   canonical database digests are saved as sanitized CI artifacts.

Run locally where Docker is available:

```powershell
python scripts/run_phase3n_ha_compose.py
```

The current Windows worktree has no Docker CLI, so GitHub Actions is the first
real container runtime for this contract.

## Render two-instance Staging procedure (manual and billable)

Do not start this step until the isolated Docker job and all repository checks
are green. Do not attach a Persistent Disk.

1. Open the Render **Staging** Web Service and record the deployed commit SHA,
   current instance type/count, health result, and test start time.
2. Confirm the service points to Supabase Staging and the environment boundary
   above. Confirm no Production URL/key is present.
3. In **Scaling**, select a paid instance type if required and set two
   instances. Render charges each running instance and prorates by the second;
   before confirmation, copy the price shown by Render and calculate the
   maximum test cost for the planned duration. Stop for human confirmation.
4. Confirm both distinct instance IDs appear in logs and health checks pass.
5. Run the equivalent SIGTERM/redeploy, lease-expiry takeover, stale-fence, and
   duplicate-effect checks. Record only sanitized timestamps, IDs, tokens,
   counts, and digests.
6. Record the finish time. Immediately return to one instance and the Free plan
   where Render permits it, then verify the final configuration and health.

Changing the Render plan or instance count is an explicit manual billing action;
automation in this repository never performs it.

## Trusted evidence boundary

The model source exporter remains locked until the progress report is
`READY_FOR_TRUSTED_REVIEW`. Even then it writes a local gzip artifact only. The
two B64 repository/environment variables remain unregistered until all real
thresholds, integrity checks, HA evidence, CI, and trusted-producer review pass.
No empty, dummy, estimated, or synthetic value is acceptable.

## Remaining blockers

- Apply ordinals 20 and 21 to Supabase Staging after remote CI succeeds.
- Deploy the exact reviewed commit to Render Staging and enable capture.
- Approve and implement the per-horse staking and authoritative payout adapter.
- Approve a least-privilege result-reconciliation scheduler.
- Accumulate at least 90 days, 1,000 valid settled samples, and 100 qualifying
  bets with zero leakage/conflicts and complete results.
- Run the manual paid two-instance Render test and return the service to Free.
- Review and update the protected trusted producer only after real evidence is
  complete.
