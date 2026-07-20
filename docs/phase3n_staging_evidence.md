# Phase 3N trusted Staging evidence

## Status and boundary

Phase 3N adds a trusted, fail-closed evidence path. It does not deploy, migrate, unlock, dispatch a worker, scrape, roll back, or modify Production. The workflow can establish READY/L3 eligibility only after a separately operated Staging exercise has produced every required observation and three GitHub Environment approval boundaries have been crossed.

Repository assertions, pull-request artifacts, local files, synthetic databases, Preview-only smoke tests, or a manually edited `trusted` flag cannot establish the boundary. The evidence input has no `trusted`, `success`, `l3_eligible`, or `production_ready` field. Those values are derived by the verifier and are all false when any check is missing or malformed.

## Workflow

The manual workflow is `.github/workflows/staging-evidence.yml`. The workflow file and its verifier run only from the immutable, externally protected branch `security/phase3n-trusted-producer-v1`; the exact branch head is supplied as `trusted_producer_sha`. The separately supplied `expected_commit` must equal the current deployed `origin/develop` commit. Gate-critical workflow, verifier, test and contract files must be byte-for-byte identical between the trusted producer and the candidate or the run stops before any approval. It uses these sequential GitHub Environments:

1. `staging-migration`
2. `staging-execution-unlock`
3. `production-release`

Each Environment must have required reviewers and a deployment branch policy that admits only the intended promotion branch. Approval IDs must be distinct. A single authorized operator may approve the three separate decisions, but each decision is independently recorded. The Production approval must occur after the Staging observation.

The `staging-execution-unlock` Environment supplies one protected value named `PHASE3N_STAGING_OBSERVATION_B64`. It is the base64 encoding of sanitized observation JSON, not a credential envelope. It must contain no token, cookie, credential, connection string, raw database row, arbitrary command output, or operator filesystem path. The observation is operator-attested evidence: provider identities, integrity digests and non-synthetic exercises must be collected from the live isolated Staging resources and reviewed before approval. The workflow validates and correlates those claims but deliberately receives no provider credential.

The workflow:

- validates and canonicalizes the observation before uploading it;
- uploads the canonical observation as a run-scoped immutable artifact;
- retrieves authenticated GitHub workflow approval history with the built-in token;
- projects only stable approval, actor and Environment IDs;
- binds commit, repository, workflow ref, run ID, run attempt and artifact digest;
- verifies the final evidence against the commit-bound Phase 3M manifest;
- uploads `phase3n-staging-evidence-json`;
- creates GitHub artifact provenance attestations for the evidence and report.

Promotion consumers select the approved run through repository variables `PHASE3N_STAGING_EVIDENCE_RUN_ID` and `PHASE3N_TRUSTED_PRODUCER_SHA`. They re-query the run, require the immutable producer branch and SHA, compare run attempt and repository ID, and verify the GitHub attestation with exact source ref, source digest and signer digest before accepting the JSON report.

The workflow has no deployment step and does not receive provider credentials. Provider-side exercise and rollback remain separate, bounded operations.

## Observation schema

The observation has exact schema name `phase3n-staging-observation`, version 1, and these exact top-level fields:

- `schema_version`
- `observation_schema`
- `observed_at`
- `expires_at`
- `provider_identities`
- `phase3m_bootstrap`
- `auth_rls_idor_smoke`
- `database_cache_integrity`
- `multi_instance_crash_recovery`
- `rollback_drill`
- `saga_checks`
- `staging_checks`

Provider identities contain only stable IDs for the isolated Vercel, Render and Supabase Staging resources. Vercel and Render deployment identities must reference the exact evaluated commit. URLs and provider credentials are excluded.

`phase3m_bootstrap` binds the hosted bootstrap history to:

- the evaluated commit;
- the canonical Phase 3M chain digest;
- the canonical manifest digest;
- the hosted schema fingerprint;
- exactly the canonical migration count and history count;
- commit and replay fingerprint matches.

The Auth/RLS/IDOR section contains booleans only. It proves Free, Premium and Admin authentication, anonymous denial, per-user profile isolation, foreign-row denial, role-escalation denial, privileged browser-RPC denial and private-bucket write denial. User IDs, emails, tokens and rows are not evidence fields.

Database and cache integrity uses before/after SHA-256 digests and capture timestamps. Both pairs must match. The crash/recovery section requires two or more real instances, a real injected crash, convergence, zero duplicate effects, at least one stale-fence rejection and zero orphaned operations. The rollback drill requires ordered timestamps, a restored state digest and zero unexpected effects. Both exercises must be marked non-synthetic.

The nine saga checks exactly match the Phase 3H cross-store blocker set. The five Staging checks exactly match its Staging blocker set. Every value must be the JSON boolean `true`; strings and integers are rejected.

## Final evidence and report

The final evidence schema is `phase3n-staging-evidence`, version 1. The builder adds:

- exact commit/run/repository/workflow/environment provenance;
- the source artifact ID and SHA-256 digest;
- GitHub repository and Environment resource IDs;
- three approval records with stable actor IDs and distinct derived approval IDs.

The verifier command is:

```text
python scripts/security/verify_phase3n_staging_evidence.py \
  --evidence reports/phase3n_staging_evidence.json \
  --expected-commit <40-hex-commit> \
  --expected-run-id <workflow-run-id> \
  --expected-run-attempt <positive-attempt> \
  --expected-repository <owner/repository> \
  --expected-workflow-ref <workflow-ref> \
  --expected-environment staging \
  --max-age-seconds 3600 \
  --report reports/phase3n_staging_evidence_gate.json
```

The gate report schema is `phase3n-staging-evidence-gate-report`, version 1. Its sole trusted boundary is `success=true` and `trusted=true`; the values always agree. Only that boundary may set all of the following to true:

- `saga_prerequisites_complete`
- `staging_prerequisites_complete`
- `approvals_complete`
- `l3_eligible`
- `production_ready`

Consumers must call `validate_gate_report(...)` and correlate the expected commit, run ID and repository. Exact-key validation, freshness and expiry checks, completeness checks, artifact provenance and sanitization are repeated. A missing approval, stale observation, provider mismatch, Phase 3M mismatch, unknown key, raw value, unsafe path or malformed report fails closed.
