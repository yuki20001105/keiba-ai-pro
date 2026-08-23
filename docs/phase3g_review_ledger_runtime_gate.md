# Phase 3G Review Ledger Runtime Gate

## 1. Status and Scope

Phase 3G adds runtime contract evidence for the Phase 3F uncertainty-review ledger and an Admin-only reviewer console. It remains **L2 (code/CI contract-ready)**. It does not claim L3.

Implemented in this phase:

- An Admin-only, review-only queue at `/data-collection/uncertainty-reviews`.
- Explicit loading of reviewable requests; the page performs no automatic request on mount.
- Independent approve/reject recording through the existing server-authoritative review APIs.
- A disposable PostgreSQL runtime gate using `postgres:17.6-bookworm@sha256:f3bd19c606e442c3d7bdfa8002e03fe260a1023351e0ea4598032022b68dd6e3` with `--network none` and no published host port.
- A strict, sanitized runtime-evidence verifier tied to the tested commit and the Phase 3F migration hash.

Explicitly not implemented or authorized:

- Applying `supabase/migrations/20260718_scrape_uncertainty_review_ledger.sql` to an external Supabase project.
- Execution reservation, reservation consumption, partial unlock, lock release, scrape execution, retry, or automatic action.
- Treating an approve decision as an execution token.
- Production or staging deployment/migration operations.

Every review decision remains fixed to:

- `approval_scope=review_only`
- `execution_enabled=false`
- `lock_release_allowed=false`
- `automatic_action_taken=false`

## 2. Reviewer Console Boundary

The reviewer console is a separate Admin surface. The client profile guard hides the page and parent navigation from non-Admins, while every ledger API independently re-verifies the Admin role as the authoritative boundary. It does not weaken the Phase 3E/3F uncertainty lock.

- Requests are loaded only after explicit operator action from `GET /api/scrape/uncertainty-review-requests?scope=reviewable&limit=20`.
- Only strict, pending, unexpired, review-only records are rendered.
- Unknown fields, duplicate records, unsafe flags, malformed values, and expired pending records fail closed.
- Approve/reject requires an explicit review-only acknowledgement and a normalized 20-500 character reason.
- The decision body is limited to `action`, `expected_version`, and `reason`.
- The returned decision is correlated to the selected request, action, version, and reason before the UI accepts it.
- Synchronous single-flight guards prevent duplicate load and decision submissions.
- The page performs no scrape write, unlock, retry, navigation, or `localStorage` mutation.

## 3. Disposable Runtime Gate

The release-blocking runtime gate is implemented by:

- `scripts/security/run_phase3g_review_ledger_runtime_gate.py`
- `supabase/tests/phase3g_review_ledger_bootstrap.sql`
- `supabase/tests/phase3g_review_ledger_runtime_contract.sql`

The gate:

1. Requires a local Docker context and rejects remote Docker endpoints.
2. Pulls the immutable digest-pinned PostgreSQL image on the CI host, then starts it with `--network none`.
3. Publishes no database port and executes `psql` over the container's Unix socket.
4. Creates only disposable synthetic roles, profiles, clock data, requests, and events.
5. Applies the Phase 3F migration and replays it to verify idempotent compilation.
6. Verifies catalog boundaries, RLS posture, service-role-only RPCs, fixed `search_path`, immutable events, review-only constraints, and the absence of execution RPCs.
7. Verifies idempotent request creation, self-approval rejection, CAS conflicts, concurrency serialization, expiry materialization, event immutability, and review-only flags.
8. Removes the container on both success and failure and reports whether cleanup was confirmed.

The gate does not use a Supabase URL, database DSN, external secret, or persistent volume. The CI host may contact the image registry for the digest-pinned pull; the PostgreSQL container itself has no external network. It therefore proves the migration/RPC contract in an isolated runtime, not in staging.

## 4. Evidence Contract

The runtime producer writes:

- `reports/phase3g_review_ledger_runtime.json`

The verifier is:

- `scripts/verify_phase3g_runtime_evidence.py`

It writes:

- `reports/phase3g_runtime_evidence_gate.json`

The verifier is fail-closed. It requires an expected commit in every mode and enforces an exact schema, duplicate-key rejection, bounded file size, UTF-8/JSON validity, commit correlation, migration SHA-256 correlation using LF-canonicalized SQL bytes so Windows and Linux checkouts agree, freshness/future-skew bounds, exact catalog/behavior/cleanup booleans, and rejection of path-, DSN-, secret-, and raw-row-like content. It is a contract checker, not a trusted attestation producer: staging-shaped input cannot self-assert L3, so verifier output remains `l3_eligible=false` until an independently authenticated producer boundary exists.

Synthetic evidence produced by this gate must contain:

- `evidence_mode=synthetic`
- `environment=ci-disposable`
- `database_scope=disposable_docker`
- `network_mode=none`
- `synthetic=true`
- `l3_eligible=false`

A compatible synthetic report can pass the L2 runtime contract gate, but it can never be used as L3 evidence.

## 5. Local and CI Invocation

From the repository root with a local Docker daemon:

```powershell
python scripts/security/run_phase3g_review_ledger_runtime_gate.py
$commit = git rev-parse HEAD
python scripts/verify_phase3g_runtime_evidence.py `
  --evidence reports/phase3g_review_ledger_runtime.json `
  --expected-commit $commit `
  --max-age-seconds 900
```

CI runs the producer and verifier in a dedicated release-blocking job and uploads both sanitized JSON reports as the `phase3g-review-ledger-runtime-json` artifact.

## 6. Remaining L3 Blocker

The authoritative review ledger is in Supabase/PostgreSQL, while scrape jobs are currently persisted in local SQLite. An atomic execution reservation/consume operation and job creation therefore cross two persistence systems.

No safe atomic protocol exists in the current implementation. Before any unlock or execution work, the project requires an explicit cross-store design, such as a durable saga/outbox with idempotent compensation and recovery semantics. Until that design is implemented and controlled staging evidence is captured:

- reservation/consume remains unimplemented;
- review approval cannot unlock or execute a scrape;
- the external Phase 3F migration remains unapplied by this repository workflow;
- Phase 3G remains L2 and L3 remains unclaimed.
