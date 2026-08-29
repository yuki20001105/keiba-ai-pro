# Supabase fresh-project bootstrap v1

This directory is the canonical, ordered bootstrap for a **new and isolated**
Supabase project. It reconciles the repository's historical schema files into
one fail-closed chain without rewriting already shipped migrations.

The only accepted ordering authority is the canonical
`supabase/bootstrap/v1/manifest.json`; the gate and renderer reject every
alternate manifest path, including a content-equivalent copy. The Phase 3M gate applies the chain
to two independent, disposable PostgreSQL databases, validates the resulting
security contract, and compares their schema fingerprints. It never connects
to a remote database and runs Docker with networking disabled.

Do not run the historical root-level `supabase/*.sql` files against Staging or
Production. Several of them are setup snapshots rather than forward-only
migrations and include destructive or unsafe development defaults.

## Promotion boundary

1. The release-blocking disposable bootstrap gate must pass for the exact Git
   commit.
2. A human must confirm the target is the isolated Staging project.
3. Render the exact commit-bound transaction with
   `scripts/security/render_phase3m_supabase_bootstrap_sql.py`. The generated
   bundle includes a hosted-Supabase-safe empty-application preflight and an
   internal content-addressed migration history. Every history row records the
   validated 40-character Git commit SHA embedded as a fixed SQL literal, so
   the applied chain remains attributable to the exact rendered commit. Apply that bundle using a
   short-lived privileged session. Never place the connection string or
   service-role key in an artifact or shell transcript.
4. Run authenticated RLS/IDOR smoke tests against Staging.
5. Capture commit-bound, sanitized evidence before considering L3 eligibility.

The v1 identity trigger is intentionally email-only. Supabase Auth must keep
anonymous users and phone-only sign-in disabled for projects using this chain;
otherwise user creation fails closed instead of creating a partial profile.

Re-applying this bootstrap directly to an already initialized database is not
supported. Rollback before business data exists is project recreation; after
business data exists it requires a separately reviewed forward migration.

The repository's legacy `supabase/migrations` directory contains duplicate
historical versions, so `supabase db push` is not an approved promotion path.
The manifest, commit-bound renderer, and `phase3m_internal.bootstrap_history`
are authoritative until those historical files are separately quarantined.
