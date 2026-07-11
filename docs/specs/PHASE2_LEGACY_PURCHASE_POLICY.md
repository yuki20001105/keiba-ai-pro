# Phase 2 Legacy Purchase Policy (SQLite fallback)

## Policy
- Existing records with user_id IS NULL or empty are treated as legacy orphan rows.
- Orphan rows are not auto-assigned to any user.
- Normal user APIs never return orphan rows.
- Update/Delete requires strict user_id ownership match.
- Not found or non-owner returns 404 to prevent information leakage.

## Migration behavior
- Schema migration is idempotent: user_id column is added only when missing.
- Migration execution is embedded in runtime path via _ensure_tracking_user_column().

## Diagnostics
- Admin-only read endpoint: /api/purchase/diagnostics/legacy-orphans.
- Returns orphan count for migration/audit planning.

## Security expectations
- Unauthorized access should not modify row counts.
- Cross-user update/delete attempts are rejected and leave DB unchanged.
