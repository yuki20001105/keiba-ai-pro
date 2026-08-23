# Phase 3L Staging Readiness Gate

## 1. Status and scope

- Phase: **3L-A (fail-closed deployment baseline)**
- Maturity: **L2**
- Production verdict: **NOT_READY**
- `production_ready=false`
- `l3_eligible=false`
- External changes performed by Phase 3L-A: **0**

Phase 3L-A hardens repository runtime defaults, CORS configuration, deployment manifests, release workflows, tests, documentation, and the production environment-variable template. It disables the legacy scheduler outside local/test, removes the saga-external scheduled scrape path, and blocks release claims while READY/L3 evidence is absent. It does not create or update a Vercel/Render environment, GitHub Environment, ruleset, branch protection rule, secret, deployment, domain, database, Supabase project, migration, worker, scheduler instance, or execution lock.

## 2. Backend URL source of truth

The deployed Next.js server routes use the following server-side variables as the authoritative FastAPI origins:

| variable | authority | required outside local development | rule |
|---|---|---:|---|
| `ML_API_URL` | general FastAPI base URL | yes | absolute HTTPS origin; no path/query/fragment; not localhost |
| `SCRAPE_API_URL` | scrape/profiling FastAPI base URL | yes | absolute HTTPS origin; no path/query/fragment; not localhost |
| `NEXT_PUBLIC_API_URL` | legacy/public compatibility fallback | only where a browser consumer still requires it | must not replace either server-only authority; if set, use the same approved origin |

`NEXT_PUBLIC_ML_API_URL` and `NEXT_PUBLIC_SCRAPING_API_URL` are not consumed by the current implementation and must not be configured as substitutes. Secrets must never use a `NEXT_PUBLIC_` name.

Deploy validation must fail closed when either authoritative variable is absent, malformed, uses HTTP, resolves to loopback/link-local/private infrastructure not explicitly owned by the staging topology, or points at the production backend from staging.

## 3. Fail-closed deployment variables

Every non-local environment must explicitly provide the following values. Platform defaults or application localhost fallbacks are not acceptable deployment evidence.

```env
APP_ENV=production
ML_API_URL=https://api.example.com
SCRAPE_API_URL=https://api.example.com
NETKEIBA_RACE_WRITE_ENABLED=false
ALLOW_STAGING_WRITE=false
PRED_LIMIT_ALLOW_FAIL_OPEN=false
SCHEDULER_ENABLED=false
PHASE3J_SAGA_RUNTIME_MODE=disabled
PHASE3J_REMOTE_EFFECTS_ENABLED=false
PHASE3J_WORKER_DISPATCH_ENABLED=false
PHASE3J_EXECUTION_UNLOCK_ENABLED=false
```

For Staging, `APP_ENV=staging` is required, but all write/unlock flags above remain `false` until a separately approved bounded exercise. Phase 3L-A does not authorize that exercise. `SUPABASE_SERVICE_ROLE_KEY`, `INTERNAL_SECRET`, Stripe secrets, and other credentials belong only in the platform secret store and must never appear in evidence or committed files.

## 4. External manual prerequisites

The following are external prerequisites, not repository facts and not completed by this phase:

1. Create a dedicated **Staging Environment** in Vercel and an isolated staging backend environment in Railway/Render (whichever provider is actually selected).
2. Keep Vercel Production Branch fixed to `main`; prove that `develop` and feature branches can produce only Preview/Staging deployments.
3. Create a GitHub `staging` Environment with required reviewers, protected secrets, deployment-branch restrictions, and no self-approval path.
4. Configure repository rulesets/branch protection for `develop`, `main`, and `release` as applicable. Required checks must include every release-blocking CI gate, and bypass/admin paths must be explicitly reviewed.
5. Use a staging-only Supabase project/database and staging-only credentials. Production credentials, database URLs, service-role keys, storage buckets, and webhook secrets must not be shared.
6. Register `ML_API_URL` and `SCRAPE_API_URL` independently in each platform environment and retain metadata-only evidence that both are present; never capture their secret values.
7. Approve migration application, staging deployment, bounded external HTTP, write unlock, and production promotion as separate manual decisions. One approval cannot imply another.

## 5. Required evidence shape

Evidence must be sanitized, immutable, commit-bound, environment-bound, and collected from authenticated provider APIs or exported settings. It may contain resource IDs, branch names, timestamps, deployment/environment labels, variable names and hashes, but never variable values, tokens, cookies, DSNs, raw database rows, or absolute operator paths.

A self-authored markdown statement, local `.env` file, Preview deployment, disposable CI database, or synthetic test cannot attest Staging or L3.

## 6. Phase 3L-B exit criteria

Phase 3L-B may pass only when all criteria below are independently verified:

1. An authenticated Vercel readout proves Production Branch=`main`, a distinct Staging environment exists, and the evaluated commit has no Production deployment.
2. An authenticated backend-provider readout proves a distinct staging service/domain, HTTPS health response, and no production credential/resource reuse.
3. GitHub API evidence proves the `staging` Environment has required reviewers and deployment-branch restrictions.
4. GitHub API evidence proves required rulesets/branch protection and required status checks for the promotion branches, with bypass actors explicitly enumerated and accepted.
5. Metadata-only environment-variable evidence proves `ML_API_URL` and `SCRAPE_API_URL` exist in the intended scopes, are distinct from localhost, and resolve to the approved staging backend; no value is written to logs/artifacts.
6. All write, fail-open, scheduler and Phase 3J execution flags remain disabled. Any missing or truthy value fails the gate.
7. Staging Supabase isolation and the exact unapplied/applied status of every required migration are recorded. Applying a migration requires a separate explicit approval and rollback plan.
8. A commit-bound staging evidence artifact is produced by a trusted workflow, passes independent review, is fresh, and reports `production_ready=false` until later execution and rollback evidence is complete.
9. No production deploy, promotion, write, worker dispatch, scheduler start, migration, secret rotation, or execution unlock occurs during readiness inspection.

Passing Phase 3L-B establishes an environment-readiness prerequisite only. L3 still requires controlled staging execution evidence for the designated flows; Production READY requires additional release approval and all Production-readiness blockers to be closed.

## 7. Current blockers

- Staging Environment topology has not been authoritatively verified.
- Repository rulesets/branch protection and required reviewer policy are not proven configured.
- Staging-only backend/Supabase isolation is not proven.
- Deployment-variable presence and scope are not yet attested by provider APIs.
- Required external migrations and controlled staging evidence remain separately unapproved/uncollected.

Therefore Phase 3L-A remains **L2 / Production NOT_READY / `l3_eligible=false`**.
