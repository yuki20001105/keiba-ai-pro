# Phase3N official history, point-in-time odds, and OOF value runbook

## Safety boundary

This path is research-only. It does not approve, activate, or deploy a model or
wagering policy. Historical replay is not prospective Staging evidence. Data
after 2026-07-11 remains reserved and must not be inspected during development.

Two sources are deliberately separate:

- JRA official annual-result PDFs supply settled outcomes for speed-deviation
  training. Their printed odds are final result values and never enter the
  training payload or satisfy the point-in-time-odds gate.
- Prospective Staging observations supply decision-time odds for value/ROI
  evaluation. Historical result PDFs must never be relabeled as prospective
  evidence.

Use the JRA path for internal research only and preserve the official
`https://www.jra.go.jp/use/` reference with every bundle. No subscription key,
provider contract, netkeiba scraping, or synthetic outcome is involved.

## Verified research snapshot (2026-08-15)

- 1,729 indexed and downloaded official PDFs for 2019-2024
- 19,846 parsed flat races and 273,187 timed runners
- 755 jump races excluded by policy
- 87 flat-race tables excluded with a recorded non-guessing reason
- 3 source PDFs excluded for unsupported font encoding
- manifest SHA-256: `4e52d5f478fddfcdbc43aa405a37013f8bb002ec9aaf59d2db43c37e063d399e`
- dry-run: 273,187 records validated, database unchanged
- append-only research copy: 273,187 records inserted; `PRAGMA quick_check=ok`
- 2020-2024 expanding-fold report: `speed_deviation_walk_forward_20260815T065131Z.json`
- aggregate: 229,190 runners / 16,644 races, AUC 0.71369, Spearman 0.65260,
  Brier 0.06455, ECE 0.00545; AUC failed and ROI/DD stayed unavailable

This snapshot is research evidence only. It did not activate or deploy a model.

## 1. Build the official source index

```powershell
python scripts/prepare_jra_official_history.py index `
  --years 2019 2020 2021 2022 2023 2024 `
  --output C:\approved-jra\source-index.json
```

This reads six official annual index pages only. It records every PDF URL,
index fetch time, target year, purpose, and terms reference without downloading
the PDF bodies.

## 2. Download exact official bytes at a bounded rate

Pilot one file first:

```powershell
python scripts/prepare_jra_official_history.py download `
  --index C:\approved-jra\source-index.json `
  --download-root C:\approved-jra\pdf `
  --output C:\approved-jra\downloaded-index-pilot.json `
  --max-files 1 `
  --workers 4 `
  --delay-seconds 1.0
```

After pilot validation, omit `--max-files` for all indexed documents. The
downloader accepts only HTTPS files on the official JRA annual-result path,
checks the PDF signature/content type, waits at least 0.5 seconds between
request starts across at most eight bounded workers, writes through a partial
file, and stores URL, byte length, UTC download time, and SHA-256.

## 3. Convert without importing final odds as features

```powershell
python scripts/prepare_jra_official_history.py convert `
  --index C:\approved-jra\downloaded-index-full.json `
  --bundle-dir C:\approved-jra\bundle `
  --workers 4 `
  --require-complete-index
```

The converter splits each landscape page visually into its two race columns,
extracts date, venue, race number, distance, surface, post time, horse number,
name, finish, and time, and fails closed on unknown column-count drift. Jump
races and explicit non-finishers are audited but excluded from speed targets.
A race whose printed tenth-of-second glyph or runner-table layout cannot be
recovered is excluded as a whole rather than guessed, and its reason remains
in the conversion report.
Result-time win odds are stored in a sidecar audit column;
`odds`, `odds_observed_at`, and `popularity` are forbidden in the training
payload.

## 4. Dry-run, then append to a recoverable research DB copy

```powershell
python scripts/import_jra_official_history.py `
  --manifest C:\approved-jra\bundle\manifest.json

python scripts/import_jra_official_history.py `
  --manifest C:\approved-jra\bundle\manifest.json `
  --db C:\approved-research\keiba_ultimate.db `
  --apply
```

The default is a no-write dry run. Apply is transactional, append-only, and
idempotent. Official rows remain in separate `official_history_imports` and
`official_history_entries` tables. Complete licensed races, if ever supplied,
outrank official result races; official races outrank overlapping legacy
scraper races. No existing source bytes are deleted.

## 5. Audit speed coverage separately from value coverage

```powershell
python scripts/audit_phase3n_research_data.py `
  --db C:\approved-research\keiba_ultimate.db
```

`ready_for_speed_deviation_walk_forward` requires the configured race minimum
for every year from 2019 through 2024 using official/licensed outcomes.
`ready_for_oof_value_evaluation` remains false until genuine timestamped
decision-time odds exist. This separation is intentional.

## 6. Run annual speed-deviation walk-forward

Run 2020 through 2024 as expanding outer folds only after the outcome audit
passes. Fold-year outcomes are never used to fit that fold's baseline,
features, tree count, or probability temperature. The report must mark ROI/DD
unavailable for folds without decision-time odds; unavailable value gates do
not become passes.

The ability matrix excludes `odds`, `popularity`, all quote timestamps/source
metadata, and derived market probabilities. Result-time odds may be retained
only in the separate audit column and must not re-enter the ability model.

## 7. Collect prospective odds and complete value evaluation

Continue append-only Staging observations for 90 days, 1,000 evaluation
samples, and 100 qualifying wagers. Once enough timestamped quotes exist,
generate base speed-model OOF predictions, fit the OOF winner layer, select
EV/edge/odds-band/bet-rate rules on inner meta-OOF only, freeze the numeric
rule, and evaluate an untouched later period.

The older licensed-export contract remains below for a future explicitly
authorized structured source. It is not required for the current free route.

## Appendix A. Optional licensed export contract

Create one or more UTF-8 JSONL files and a manifest beside them. Every JSONL
row uses `licensed-keiba-history-runner-v1` and represents one settled runner.
The complete race must be present, with at least five distinct horse numbers
and exactly one winner. `payload` may include additional pre-race features but
must include `distance`, `surface`, `finish`, `time_seconds`, and
`horse_number`.

Each runner also carries one or more win-odds snapshots. A snapshot has
`odds`, `observed_at`, `source`, and `snapshot_kind`. The default decision
contract selects the latest non-final quote observed no later than five minutes
before post time and no more than 30 minutes old. Result/final odds, post-cutoff
quotes, stale quotes, missing timestamps, incomplete races, and hash mismatches
fail closed.

Minimal manifest shape:

```json
{
  "schema": "licensed-keiba-history-manifest-v1",
  "provider": "JRA-VAN Data Lab",
  "license_reference": "local subscriber export reference",
  "exported_at": "2026-08-15T00:00:00Z",
  "complete_races": true,
  "files": [
    {
      "path": "runners-2019.jsonl",
      "sha256": "64 lowercase hexadecimal characters",
      "format": "jsonl"
    }
  ]
}
```

Never include a subscription key or other secret in either file.

### A.1 Validate before write

```powershell
python scripts/import_licensed_history.py --manifest C:\approved-export\manifest.json
```

The default is a dry run. It validates every file and record, reports coverage,
and does not modify the database.

### A.2 Append the validated bundle

Make a recoverable copy of the local research database, then run:

```powershell
python scripts/import_licensed_history.py `
  --manifest C:\approved-export\manifest.json `
  --db C:\approved-research\keiba_ultimate.db `
  --apply
```

Imports are transactional and append-only. Identical reapplication is
idempotent. A conflicting source record aborts the transaction. Licensed rows
remain in separate `licensed_history_imports` and `licensed_history_entries`
tables; the training loader gives the licensed canonical row priority over an
overlapping legacy scraped row without deleting either source.

### A.3 Audit readiness

```powershell
python scripts/audit_phase3n_research_data.py --db C:\approved-research\keiba_ultimate.db
```

Do not run OOF value folds until all licensed rows have timestamped quotes. A
year with ordinary legacy/final odds but no observation timestamp does not
satisfy the point-in-time contract. Override the minimum only with a reviewed
coverage rationale.

### A.4 Generate base speed-model OOF predictions

For every inner year, train the speed-deviation model only on earlier years.
Export one row per runner with `base_prediction_is_oof=true`, `base_score`,
winner label, post time, and the selected point-in-time odds provenance. Outer
predictions must be generated by a model trained before the outer period and
must be strictly later than every inner OOF row.

Accepted interchange formats are Parquet and JSONL. CSV is deliberately not
accepted because timestamp and type coercion is too easy to miss.

### A.5 Fit the OOF winner layer and nested strategy search

```powershell
python scripts/evaluate_phase3n_oof_value.py `
  --inner-base-oof C:\approved-research\inner-base-oof.parquet `
  --outer-base-predictions C:\approved-research\outer-base.parquet
```

The winner meta-model uses only OOF speed scores and decision-time market
features. Meta-level OOF folds are again expanding-year splits. EV threshold,
probability edge, odds band, and target bet-rate conditions are selected only
on those inner meta-OOF predictions. The chosen numeric condition is frozen
before outer evaluation.

Generated model/report artifacts are marked candidate-only, unapproved,
inactive, undeployed, and not deployment-eligible. A later policy change needs
its own durable business approval; it cannot inherit the existing
`phase3n-tansho-flat-v1` approval.
