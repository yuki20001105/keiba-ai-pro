# Phase 3H Production Readiness Decision Gate

## 1. Decision

Phase 3H is a release-blocking decision-contract gate. Its correct current result is:

- `verdict=not-ready`
- `production_ready=false`
- `l3_eligible=false`

This result is not a failed develop CI run. Develop CI succeeds when the gate proves, without ambiguity, that production prerequisites are incomplete and emits the complete schema-version-1 blocker set. Malformed, stale, inconsistent or self-promoting input fails the gate.

For ordinary develop runs, the current repository-only assessment remains `not-ready`. For a `develop -> main` promotion, CI accepts READY only from the separate Phase 3N trusted Staging workflow running at the immutable `security/phase3n-trusted-producer-v1` SHA: the exact candidate commit, selected successful workflow run and attempt, GitHub-signed artifact provenance, repository ID, Environment approvals, freshness, provider identities, Phase 3M bootstrap fingerprints, Saga checks and Staging checks are all revalidated. Missing, ambiguous, stale, unsigned or mismatched evidence fails closed. A green develop assessment must never be interpreted as production authorization.

## 2. Inputs

The gate has two mutually exclusive input modes:

1. Develop assessment: the sanitized Phase 3G PostgreSQL runtime artifact produced earlier in the same workflow run, plus the versioned repository manifest describing currently absent prerequisites.
2. Promotion assessment: the Phase 3N trusted Staging gate report downloaded from the unique successful run for the exact candidate, plus its expected run ID and repository identity.

Every invocation requires the expected 40-hex tested commit. The Phase 3G artifact is revalidated with its existing strict verifier, including freshness, commit correlation, migration SHA-256, catalog checks, behavioral checks and cleanup.

The repository manifest has exact, duplicate-free JSON schemas for:

- cross-store saga/outbox/compensation capabilities;
- controlled staging evidence;
- explicit migration, execution-unlock and production-release approvals.

All repository-manifest values remain `false`. Setting any value to `true` is rejected as an unsupported self-claim; a repository edit is not a trusted attestation. Only the strictly validated and GitHub-provenanced Phase 3N artifact can produce a READY decision.

## 3. Required Saga/Outbox Evidence

Production readiness remains blocked until independently authenticated evidence proves all of the following:

- one stable `operation_id` and predetermined `job_id` bind review, command and job;
- reservation and consume are idempotent, single-use and expiry-aware;
- SQLite job, saga and outbox records are prepared in one durable transaction;
- no worker can start before a reservation is durably consumed;
- recovery and compensation are durable and idempotent;
- dispatcher leases use fencing tokens and reject stale workers;
- concurrent and multi-instance dispatch cannot duplicate an effect;
- the full failure-injection matrix converges without unlocking or starting a scrape.

The normal develop assessment records absence of these controls as blockers. The promotion assessment requires Phase 3N to demonstrate every item against isolated Staging before READY can be emitted.

## 4. Required Staging Evidence

The following must be produced by the trusted Phase 3N Staging workflow, not by a pull-request manifest:

- the review-ledger migration was explicitly approved and applied to staging;
- bounded external-HTTP validation ran against staging-owned configuration;
- operational databases and caches were proven unchanged for read-only validation;
- a non-synthetic crash/recovery matrix was executed against the real deployment topology;
- evidence provenance is authenticated independently of the repository under test.
- repository rulesets or required workflows protect the promotion check from being weakened by the pull request being evaluated.

## 5. Required Human Approvals

Three explicit GitHub Environment approval boundaries are required:

- staging migration approval;
- execution unlock approval after saga and staging evidence pass;
- production release approval.

Approval state is never inferred from a Git commit, review-ledger `approved` status, browser storage or UI interaction.

## 6. Safety Boundary

Phase 3H performs no scrape request, retry, unlock, reservation, consume, compensation, deployment or database migration. It does not change the Phase 3E client lock or the Phase 3F/G review-only ledger semantics. The gate output is sanitized: no raw rows, secrets, DSNs, absolute paths or arbitrary operator text are copied into the report.

## 7. CI Contract

For ordinary develop validation, the independent job `Phase3H production readiness decision (release-blocking)`:

1. waits for the Phase 3G runtime job;
2. downloads only the Phase 3G artifact from the same workflow run;
3. revalidates it against `GITHUB_SHA` with a bounded queue-delay allowance;
4. derives the exact `not-ready` blocker set;
5. uploads `phase3h-production-readiness-json` even though no READY/L3 claim is made.

For develop, this remains L2 evidence and a successful `not-ready` assessment. For `develop -> main` promotion, the job resolves exactly one successful Phase 3N workflow run for the candidate, downloads its gate report, verifies GitHub artifact provenance against `.github/workflows/staging-evidence.yml`, and invokes this verifier with `--require-ready`. The post-merge release authorization workflow independently repeats the run provenance, signed-artifact and READY checks. Neither mode can establish L3 or production readiness from repository self-claims.
