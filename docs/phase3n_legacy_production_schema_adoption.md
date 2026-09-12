# Phase 3N legacy Production schema adoption

> The 21-migration and 21-history-row values below are immutable facts about
> the approved 2026-08-22 adoption candidate and its retained evidence. The
> current repository candidate now appends ordinal 22 for operational scrape
> cancellation. That new ordinal is not part of the recorded hosted history
> unless a separately authorized append-only upgrade is applied; existing
> history rows and evidence must not be rewritten.

## Decision and boundary

Production Supabase project `grfwkutcsavqicaimssn` is a live legacy schema,
not an empty Phase 3M/3N target and not an older canonical manifest prefix. The
fresh bootstrap renderer must reject it, and the append-only upgrade renderer
must reject it because `phase3m_internal.bootstrap_history` is absent. Neither
renderer may be bypassed or supplied with fabricated history.

The selected migration is an in-place, single-transaction archive and
canonical rebuild:

1. verify the exact legacy table set, row counts, row SHA-256 values,
   referential checks, JSON conversion checks, Auth identities, and model
   Storage references;
2. move every legacy public table and the legacy ML view into the private
   `phase3n_legacy_20260816` schema without deleting a row;
3. apply the exact 21-migration canonical manifest from candidate
   `86a2d314a641160e852d3597396aadcd03e81347` to the newly empty public schema;
4. copy only reviewed legacy datasets into their canonical tables, decoding
   the three double-encoded JSONB datasets during the copy;
5. verify both the archived source and canonical destination before commit.

Auth users and model Storage objects remain in place. This avoids cross-project
Auth migration, secret transfer, object copying, and DNS/provider cutover while
retaining the original legacy rows as a private immutable recovery source.

This document and its generated SQL are a review package only. They do not
authorize a Production migration, provider setting change, deployment, or
observation write.

## Read-only findings

The 2026-08-16 catalog review used only SELECT statements. It found 24 public
legacy tables, one `ml_training_data` view, one public sequence, zero tracked
migrations, and zero Phase 3N tables. The rows that require preservation are:

| Table | Rows | Migration treatment |
| --- | ---: | --- |
| `profiles` | 3 | Copy to canonical profile/quota columns; Auth FK already valid |
| `purchase_history` | 4 | Copy with canonical defaults and checks |
| `races` | 288 | Copy to the merged canonical race shape |
| `race_results` | 9,588 | Copy existing fields; new canonical aliases remain nullable |
| `race_payouts` | 5,847 | Copy after verified race-parent integrity |
| `races_ultimate` | 72 | Decode JSONB string to JSONB object |
| `race_results_ultimate` | 719 | Decode JSONB object and derive required `horse_number` |
| `model_metadata` | 73 | Decode metadata, retain Storage path, set shared owner |
| `horse_pedigree` | 1,805 | Copy and add canonical update timestamp |
| `users` | 1 | Preserve in archive only; canonical identity is `auth.users` + `profiles` |

All remaining 14 legacy tables currently contain zero rows. There are zero
race/result/payout/ultimate orphans, zero profiles without an Auth user, zero
purchase records without a profile, and zero invalid role/tier values. All 72
race blobs, 719 result blobs, and 73 metadata blobs are JSONB strings that
decode to objects. Every decoded result contains a horse number and the
`race_id + horse_number` pairs are unique. The `models` bucket exists with 146
objects and every one of the 73 metadata paths resolves to an object.

One separate legacy system UUID is referenced by all 288 `races`, 9,588
`race_results`, and 5,847 `race_payouts` rows but exists in neither
`profiles` nor `auth.users`. These optional canonical FKs therefore become
`NULL` during the reviewed copy. This does not delete or rewrite a domain row:
the original UUID and complete source rows remain byte-verifiable in the
private archive schema. The preflight fixes the exact affected counts, and the
postcondition requires both the expected canonical NULL counts and zero
remaining non-NULL orphan references.

The sanitized counts and aggregate hashes are in
`reports/evidence/phase3n/phase3n_legacy_production_adoption_contract_20260816.json`. No row
payload, user email, credential, object name, or connection string is stored.

## Artifacts and generation

- `scripts/security/render_phase3n_legacy_production_preflight_sql.py` emits
  `reports/generated/phase3n/phase3n_legacy_production_preflight.sql`. It begins a
  `REPEATABLE READ, READ ONLY` transaction and fails if any reviewed count,
  aggregate digest, table identity, relationship, decoded JSON shape, Auth
  link, or Storage reference has changed.
- `scripts/security/render_phase3n_legacy_production_adoption_sql.py` emits
  `reports/generated/phase3n/phase3n_legacy_production_adoption_review.sql`. The complete
  migration is present for review, but review scope always places an
  unconditional exception before the first schema change. The separately
  rendered, digest-bound Production artifact is
  `reports/evidence/phase3n/phase3n_legacy_production_adoption_apply.sql`.
- Generated SQL remains outside Git. After review, an approved immutable snapshot
  may be promoted deliberately into `reports/evidence/phase3n/`; renderers never
  overwrite the reviewed evidence by default.
- The review SQL contains no `DROP TABLE`, `TRUNCATE`, or source-row `DELETE`.
  It has no provider credential and no remote apply capability.

Render the current review artifacts into `reports/generated/phase3n/` with:

```powershell
python scripts/security/render_phase3n_legacy_production_preflight_sql.py `
  --expected-commit 86a2d314a641160e852d3597396aadcd03e81347

python scripts/security/render_phase3n_legacy_production_adoption_sql.py `
  --expected-commit 86a2d314a641160e852d3597396aadcd03e81347
```

The owner approved Production application on 2026-08-22 under
`codex-user-instruction-2026-08-22-production-migration-approval`. The approved
contract is retained separately as
`reports/evidence/phase3n/phase3n_legacy_production_adoption_contract_approved_20260822.json`
with canonical SHA-256 `27372764c3e577e0cf9d158d50dbf2673bd7b05edfc6739911985740ece589e8`;
the resulting Production apply SQL has SHA-256
`42249f1ab036e800ef66873026acfd6f58403a55066d0eaf4aaaef1b595220a7`.
The renderer continues to require the exact digest, approval reference,
Production project ref, and candidate SHA and fails closed on any mismatch.

The renderer also has a separate `disposable-clone` scope. It requires the
exact contract digest, the recorded clone approval reference, and a 20-letter
project ref different from Production. Production authorization remains false.
An optional clone-only fault probe raises immediately after all archive moves;
the same option is rejected for review or Production scopes.

## Disposable clone result

On 2026-08-16, data-bearing Preview `txaqkmzfqmukhocgnbce` was cloned from
Production, reached `ACTIVE_HEALTHY`, and passed the standalone read-only
preflight. The clone-only forced failure ran after all archive moves, raised as
designed, and a second preflight proved the original 24-table/count/digest
state was fully restored.

The exact 21-migration adoption transaction then committed on the clone. The
final independent audit recorded 21 history rows bound only to
`86a2d314a641160e852d3597396aadcd03e81347`, 24 archived legacy tables, 45
canonical public tables, zero public tables without RLS, zero direct anon table
privileges, 19 policies, all reviewed domain row counts, 73 model metadata
rows, and all 146 Storage objects. The adoption-adjusted canonical Auth/RLS/
IDOR behavior contract completed with fingerprint
`44e20d1c1e9a54fc40ba88ad6a8d643666e44fb6fadea56f61c96fc946772012`.
The Preview and its temporary CLI token were deleted after evidence capture.

Production has a completed physical backup `1391364483` from
`2026-08-16T10:51:14.656Z`. PITR remains disabled and no paid add-on was
enabled. The migration does not modify Storage objects; Supabase physical
backup limitations for Storage bytes are retained in the release decision.
Sanitized evidence is in
`reports/evidence/phase3n/phase3n_legacy_production_clone_validation_20260816.json`.

## Required pre-apply gates

All gates below must pass in one maintenance window. Any failure cancels the
migration.

1. A provider restore point or verified backup exists immediately before the
   window, and its restore owner and retention are recorded.
2. Observation, training, activation, automatic betting, and legacy writes are
   disabled; API traffic is drained or placed in maintenance mode.
3. The project ref, candidate SHA, manifest SHA-256, chain digest, contract
   SHA-256, and SQL SHA-256 match the reviewed approval.
4. The standalone read-only preflight returns PASS in the Production project.
5. No row count or source digest changed after the approval snapshot. If it
   changed legitimately, collect a new contract and repeat review; never edit
   a digest by hand.
6. A disposable clone/Preview execution of the exact approved SQL passes the
   canonical bootstrap contract, copy postconditions, Auth/RLS/IDOR smoke, and
   application smoke.
7. The Production Render/Vercel environment remains observation-only with
   automatic betting, training, and activation disabled.
8. The incident owner, provider console owner, and rollback decision maker are
   present for the window.

## Transaction behavior

The apply bundle uses one PostgreSQL transaction, a five-second lock timeout,
a five-minute statement/idle timeout, and a contract-specific advisory lock.
Before the first schema operation it recomputes every reviewed source count and
digest. PostgreSQL rolls back the archive moves, canonical DDL, and copy if any
preflight, migration, copy, or postcondition fails.

The original tables remain under `phase3n_legacy_20260816` after a successful
commit. Browser roles and `service_role` receive no archive-schema access. The
legacy `users` table is not promoted to canonical identity. Auth schema rows
and the 146 model Storage objects are not moved or rewritten.

## Rollback conditions

Rollback is mandatory when any of the following occurs:

- preflight identity/count/digest mismatch;
- lock or statement timeout;
- canonical migration or 21-row history mismatch;
- archived-source count/digest mismatch after the move;
- canonical destination count mismatch;
- Auth, RLS, IDOR, Storage, or required RPC postcondition failure;
- exact candidate/manifest/contract binding mismatch;
- Production smoke returns non-2xx, writes outside observation scope, loads an
  unapproved model, or exposes automatic betting/training/activation;
- a provider or incident owner cannot verify the result.

Rollback has three distinct boundaries:

1. **Before COMMIT:** allow the transaction to abort. Do not retry in the same
   session. Re-run the standalone preflight and investigate the first failure.
2. **After COMMIT but before application writes:** keep provider traffic
   stopped. Restore the recorded provider backup/restore point if canonical
   smoke fails. Do not run an improvised down migration or delete the archive.
3. **After any canonical write:** immediately disable observation and stop the
   release. Preserve both canonical and archive evidence. Use provider restore
   or a separately reviewed forward repair; automatic table swap-back is
   forbidden because it can discard post-cutover writes.

Application rollback is independent of database rollback. It requires a
provider-bound known-good runtime SHA and must recheck health, Auth, Production
Supabase identity, observation-only state, and automatic-betting-disabled
state. A repository SHA that has not run successfully on the provider is not a
rollback target.

## Approval and application result

The present state is `PRODUCTION_MIGRATION_APPLIED / POSTCONDITIONS_VERIFIED`.
Production was placed in maintenance by suspending the Render API. The latest
physical backup was verified, the standalone read-only preflight returned
PASS, and the approved single transaction committed. Independent audit found
21 history rows bound only to `86a2d314a641160e852d3597396aadcd03e81347`,
24 preserved archive tables, 45 canonical public tables, zero public tables
without RLS, zero direct anon table privileges, 19 policies, four Auth users,
all 146 model Storage objects, and the Phase 3N observation table. Render then
resumed and both backend and frontend health returned 200. The sanitized
execution record is
`reports/evidence/phase3n/phase3n_production_credential_rotation_and_migration_20260822.json`.

This database result does not authorize the Limited Production runtime by
itself. The exact candidate remains undeployed to Production, automatic
betting remains disabled, and Production remains NOT_READY until the separate
environment review, observation-only deploy, monitoring, rollback, and smoke
gates pass.
