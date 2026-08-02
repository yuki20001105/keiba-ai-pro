# Repair Execution Policy

Updated: 2026-08-02
Status: direct execution disabled; approval-bound operational execution not yet released

## Decision

Data-quality repair remains an intentionally non-executable operator workflow until the durable Phase 3N saga, approval ledger, fenced destination writer, isolated Staging exercise, and rollback evidence all pass for the exact candidate commit.

The supported operator surface is currently:

- read-only P0 repair planning;
- read-only targeted-refetch planning;
- bounded Admin live validation with no database upsert;
- quality/profiling inspection and remediation recommendations.

These surfaces may identify repair candidates but grant no execution authority.

## Legacy routes

The following direct writers are compatibility utilities, not Staging or Production repair mechanisms:

- `POST /api/scrape/repair/[race_id]` -> FastAPI `/api/scrape/repair/{race_id}`;
- `POST /api/scrape/rescrape-incomplete` -> FastAPI `/api/rescrape_incomplete`;
- FastAPI legacy `/api/scrape`.

FastAPI rejects every legacy writer when `APP_ENV` is deployed or unknown. The Next repair and rescrape proxies independently reject before network access. Compatibility requires both:

1. `APP_ENV` is one of `local`, `development`, `dev`, `test`, or `ci`;
2. `PHASE3N_ALLOW_LEGACY_SCRAPE_WRITES` is explicitly `1`, `true`, `yes`, or `on`.

The repository default is disabled. This flag must never be configured in Staging or Production.

## Future executable path

Repair execution may be introduced only through the operational saga path and must satisfy all of these conditions:

1. Admin identity and durable approval/reservation are server-validated.
2. The request, candidate rows, data snapshot, code commit, and destination identity are immutable and digest-bound.
3. Reservation consumption, job creation, saga state, and outbox intent are atomic.
4. Every worker claim carries a lease and fencing token; the destination rejects stale writes.
5. Writes are bounded to allowlisted tables/columns/row counts and first proven in isolated Staging.
6. Crash-before-write, crash-after-write, replay, timeout, compensation, integrity, and rollback drills pass with non-synthetic evidence.
7. The exact trusted Phase 3N artifact derives execution eligibility; repository or UI assertions cannot unlock it.
8. Production requires a later, separate release approval and observation/rollback plan.

The current Supabase operational mode deliberately allows deployed dry-run only because the legacy destination cannot atomically validate its lease/fence. Therefore an approval record alone is insufficient to enable repair.

## Rollback and recovery boundary

Until the future path exists, rollback means no mutation occurred. Local compatibility runs are disposable and must use a fixture/copy, never a Production database. For future Staging execution, the runbook must record the pre-write snapshot/digest, bounded mutation set, compensating action, verification query, owner, and maximum recovery time before any execution approval is granted.
