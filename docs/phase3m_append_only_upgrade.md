# Phase 3M append-only hosted upgrade

## Purpose and boundary

`scripts/security/render_phase3m_supabase_upgrade_sql.py` renders a manual, commit-bound transaction for a hosted isolated Staging database that already contains an older canonical Phase 3M manifest. It exists because the fresh bootstrap renderer intentionally rejects every Phase 3M application signature.

The upgrade path never rewrites, deletes, truncates, or replaces `phase3m_internal.bootstrap_history`. It validates every existing row against one or more immutable introduction-commit segments, requires the previously applied manifest to be a byte-identical strict prefix of the candidate manifest, applies only the suffix, appends only new history rows, and rechecks the complete segmented history before commit. Any gap, overlap, changed migration byte, non-ancestor commit, partial application, unknown row, or replay fails closed.

This path preserves an operational Staging database. It does **not** make that database eligible for the current Phase 3N fresh-bootstrap attestation, which still requires all history rows to bind to one manifest-equivalent applied commit. Use a disposable isolated Preview Branch and the fresh renderer for that evidence boundary.

## Current 21-to-22 boundary

The current candidate canonical manifest contains 22 migrations. Ordinal 22
adds durable, owner-scoped cooperative cancellation for operational scrape
jobs. A hosted database whose retained evidence records 21 history rows is not
to be relabelled or rewritten: those rows are an immutable, valid prefix of the
new candidate. Advancing such a database requires a separately approved run of
this append-only renderer, an applied-manifest commit that resolves to the exact
21-entry prefix, and contiguous history segments covering ordinals 1 through
21 at their original introduction commits. This repository change does not
apply ordinal 22 to any hosted database.

## Historical 11-to-19 rendering example

The existing Staging history was created from commit `861f46c18b086578e97c15d6eaa12aed89222169` and contains ordinals 1 through 11. From an exact checkout of the candidate commit:

```powershell
python scripts/security/render_phase3m_supabase_upgrade_sql.py `
  --expected-commit (git rev-parse HEAD) `
  --applied-manifest-commit 861f46c18b086578e97c15d6eaa12aed89222169 `
  --history-segment 1:11:861f46c18b086578e97c15d6eaa12aed89222169 `
  --output reports/phase3m_supabase_append_only_upgrade.sql
```

For a later upgrade whose existing history has multiple introduction commits, repeat `--history-segment` in contiguous ordinal order. For example, an existing 19-row database produced by the first upgrade would use segments `1:11:<old-commit>` and `12:19:<second-commit>`. The applied-manifest commit must describe exactly the current schema prefix; every segment commit must be its immutable introduction source and a Git ancestor of the new candidate.

## Operator procedure

1. Collect a sanitized history projection containing ordinal, version, path, source, migration SHA-256, chain digest, bootstrap ID, manifest SHA-256, and expected commit SHA. Do not export application rows or credentials.
2. Verify the segment arguments against that projection and render from the exact candidate checkout.
3. Review the SQL digest and suffix migration list. The renderer has no provider credential or remote-apply path.
4. Enter the protected Staging migration approval boundary.
5. Apply the transaction only to the explicitly identified isolated Staging database.
6. Re-query the full history and schema fingerprint. A second execution must fail its preflight because the prior row count has advanced.
7. Retain only sanitized IDs, digests, counts, timestamps, and result codes.

The original fresh renderer remains the only path for an empty database and for the disposable Preview Branch used by Phase 3N.
