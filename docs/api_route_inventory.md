# API Route Inventory and Classification

Updated: 2026-07-05 (P1-16 sandbox write-readback verification)
Scope: Next.js API routes and FastAPI endpoints classification

## P1-16 Actual Runtime Pass Record (Staging Sandbox)

Verification scope (explicit run only):
- sandbox-precheck: pass
- sandbox-write-readback: pass
- write_performed=true only when sandbox write-readback was explicitly executed
- target_mode=sandbox for write-readback execution
- readback target tables: sandbox tables only
- production/base table write: none
- suite result: success=true, summary=warn

Operational notes:
- Staging FastAPI and local scrape stub used only for runtime verification are stopped after checks.
- Generated `reports/*.json`, model artifacts, DB artifacts, and `.env*` are excluded from commits.

## 1. Classification Rules

- production: actively used in current UI/user flow and considered stable contract
- experimental: available for trial/integration, not part of core user flow
- internal: operational/debug/webhook path; not a direct user-facing contract
- deprecated: still available for compatibility, replacement path exists
- unused: currently not used by UI/active automation (kept for reference)

## 2. Next.js API Route Classification

### production

| Next Route | Backend/Target | Current Consumer |
|---|---|---|
| /api/health | FastAPI /health | home |
| /api/data-stats | FastAPI /api/data_stats | home, dashboard, data-collection, admin |
| /api/models | FastAPI /api/models | train, predict-batch, race-analysis |
| /api/models/[id] | FastAPI /api/models/{model_id} | train |
| /api/models/[id]/activate | FastAPI /api/models/{model_id}/activate | train |
| /api/ml/train/start | FastAPI /api/train/start | train |
| /api/ml/train/status/[job_id] | FastAPI /api/train/status/{job_id} | train |
| /api/analyze-race | FastAPI /api/analyze_race | predict-batch, race-analysis |
| /api/analyze-races-batch | FastAPI /api/analyze_races_batch | backend-facing route available |
| /api/races/by-date | FastAPI /api/races/by_date | predict-batch, race-analysis, data-view |
| /api/races/recent | FastAPI /api/races/recent | data-collection |
| /api/races/[race_id]/horses | FastAPI /api/races/{race_id}/horses | data-collection, race-analysis |
| /api/realtime-odds/[race_id] | FastAPI /api/realtime-odds/{race_id} | predict-batch |
| /api/realtime-odds/refresh | FastAPI /api/realtime-odds/refresh | predict-batch |
| /api/purchase | FastAPI /api/purchase | predict-batch |
| /api/purchase-history | FastAPI /api/purchase_history | dashboard |
| /api/purchase/[id] | FastAPI /api/purchase/{purchase_id} | dashboard |
| /api/statistics | FastAPI /api/statistics | dashboard |
| /api/prediction-history | FastAPI /api/prediction-history | prediction-history |
| /api/prediction-history/[race_id] | FastAPI /api/prediction-history/{race_id} | race-analysis |
| /api/features/summary | FastAPI /api/features/summary | feature-lab |
| /api/features/importance | FastAPI /api/features/importance | feature-lab |
| /api/features/coverage | FastAPI /api/features/coverage | feature-lab |
| /api/debug/race/[race_id] | FastAPI /api/debug/race/{race_id} | data-view |
| /api/debug/race/[race_id]/features | FastAPI /api/debug/race/{race_id}/features | data-view, race-analysis |
| /api/profiling | FastAPI /api/profiling/start | data-collection |
| /api/profiling/status/[job_id] | FastAPI /api/profiling/status/{job_id} | data-collection |
| /api/profiling/html/[job_id] | FastAPI /api/profiling/html/{job_id} | data-collection |
| /api/scrape | FastAPI /api/scrape/start | data-collection, useScrape/useBatchScrape |
| /api/scrape/status/[jobId] | FastAPI /api/scrape/status/{job_id} | predict-batch, useScrape/useBatchScrape |
| /api/scrape/health | FastAPI /api/scrape/health | data-collection |
| /api/export/bet-list | FastAPI /api/export/bet-list(/csv) | predict-batch |

### experimental

| Next Route | Target | Note |
|---|---|---|
| /api/ai-correct | OpenAI/Gemini | optional OCR post-process flow |
| /api/ocr | Vision/OCR service | optional OCR flow |
| /api/netkeiba/calendar | external scrape service | utility/integration route |
| /api/netkeiba/race-list | FastAPI /api/netkeiba/race-list | migrated read-only proxy route (P1-4) |
| /api/netkeiba/race | external scrape + Supabase write | mixed write path (not canonical) |
| /api/stripe/create-checkout | Stripe | billing flow not in current core UI |
| /api/stripe/portal | Stripe | billing flow not in current core UI |

### internal

| Next Route | Target | Note |
|---|---|---|
| /api/stripe/webhook | Stripe callback | server-to-server webhook |
| /api/data/all | FastAPI /api/data/all | destructive admin utility |
| /api/export/data | FastAPI /api/export-data | ops export route |
| /api/export/db | FastAPI /api/export-db | ops export route |
| /api/debug/race-ids | FastAPI /api/debug/race-ids | diagnostic utility |
| /api/backfill/nar-pedigree | FastAPI /api/backfill/nar-pedigree | maintenance utility |
| /api/backfill/coat-color | FastAPI /api/backfill/coat-color | maintenance utility |
| /api/scrape/repair/[race_id] | FastAPI /api/scrape/repair/{race_id} | admin repair utility |
| /api/scrape/rescrape-incomplete | FastAPI /api/rescrape_incomplete | maintenance utility |
| /api/features/catalog | FastAPI /api/features/catalog | currently not used by UI |

### deprecated

| Next Route | Replacement | Reason |
|---|---|---|
| /api/scrape (current implementation calls /api/scrape/start) | /api/scrape/start style naming in Next route layer (future) | name suggests legacy sync behavior, but actual behavior is async start |

### unused (current UI)

| Next Route | Classification | Note |
|---|---|---|
| /api/features/catalog | internal | available but not used by feature-lab |
| /api/ai-correct | experimental | no active page integration found |
| /api/ocr | experimental | no active page integration found |

## 3. FastAPI Endpoint Classification

### production

- /health
- /api/data_stats
- /api/models
- /api/models/{model_id}
- /api/models/{model_id}/activate
- /api/analyze_race
- /api/analyze_races_batch
- /api/train/start
- /api/train/status/{job_id}
- /api/races/recent
- /api/races/{race_id}/horses
- /api/races/by_date
- /api/purchase
- /api/purchase/{purchase_id}
- /api/purchase_history
- /api/statistics
- /api/realtime-odds/{race_id}
- /api/realtime-odds/refresh
- /api/scrape/start
- /api/scrape/status/{job_id}
- /api/scrape/health
- /api/profiling/start
- /api/profiling/status/{job_id}
- /api/profiling/html/{job_id}
- /api/prediction-history
- /api/prediction-history/{race_id}
- /api/features/catalog
- /api/features/summary
- /api/features/importance
- /api/features/coverage
- /api/debug/race/{race_id}
- /api/debug/race/{race_id}/features
- /api/export/bet-list
- /api/export/bet-list/csv

### experimental

- /api/predict (older inference endpoint kept available)
- /api/train (synchronous training endpoint; operationally heavier than async start)
- /api/netkeiba/race-list (read-only proxy to scrape service, introduced in P1-4)
- /api/netkeiba/race/preflight (read-only preflight contract for write path decomposition, introduced in P1-5)
- /api/netkeiba/race/dry-run (write orchestration simulation without write, introduced in P1-7)

### internal

- /
- /api/debug
- /api/internal/enqueue_scrape
- /api/internal/scrape_status/{job_id}
- /api/backfill/nar-pedigree
- /api/backfill/coat-color
- /api/scrape/repair/{race_id}
- /api/rescrape_incomplete
- /api/export-data
- /api/export-db
- /api/data/all
- /api/debug/race-ids

### deprecated

- no immediate backend endpoint removal recommended in this sprint
- if deprecating /api/predict later, maintain compatibility period and migrate callers first

### unused (from current Next UI flow)

- /api/test-optuna-request
- /api/test/task
- /api/test/connectivity
- /api/models/active/info

## 4. Immediate Follow-up Candidates

1. Introduce route-level tags/metadata in API handlers for runtime-visible classification.
2. Migrate naming of Next scrape start route to explicit /api/scrape/start (keeping /api/scrape alias during transition).
3. Review mixed path route /api/netkeiba/race and converge writes to FastAPI-centric path.
4. Decide lifecycle for /api/predict and /api/train sync endpoints (retain vs deprecate).

## 5. Direct Supabase / Scrape Service Path Inventory (P1-3)

### 5.1 Supabase direct routes (Next API writes)

| Route | Direct Dependency | Current Decision | riskLevel | Why |
|---|---|---|---|---|
| /api/netkeiba/race | Supabase service-role/anon client | migrate to FastAPI | high | Next route owns data write and validation; ownership is split from FastAPI audit path |
| /api/ocr | Supabase profiles/ocr_usage write | keep for now | medium | OCR feature is optional and currently isolated; migrate after API ownership contract is prepared |

### 5.2 Scrape service direct routes (Next API -> SCRAPE_SERVICE_URL)

| Route | Direct Dependency | Current Decision | riskLevel | Why |
|---|---|---|---|---|
| /api/netkeiba/race | POST /scrape/ultimate | migrate to FastAPI | high | write path and scrape path are coupled in one route |

Migrated in P1-4:
- /api/netkeiba/race-list is no longer calling `SCRAPE_SERVICE_URL` directly from Next API.
- Path is now: Next `/api/netkeiba/race-list` -> FastAPI `/api/netkeiba/race-list` -> Scrape Service `/scrape/race_list`.

### 5.3 Responsibility matrix for direct paths

| Route | Auth check owner | DB update owner | Log/Audit owner | Failure handling owner |
|---|---|---|---|---|
| /api/netkeiba/race | Next API (input-only checks, no explicit JWT gate) | Next API (Supabase direct write) | Next API console logs | Next API response payload |
| /api/netkeiba/race-list | Next API (input-only checks) | none (read-only) | Next API console logs | Next API response payload |
| /api/ocr | Next API (userId-based checks, no backend JWT contract) | Next API (Supabase direct write) | Next API console logs | Next API response payload |

### 5.4 Classification buckets requested in P1-3

#### keep for now

- /api/ocr
	- reason: optional feature path, isolated blast radius, migration should be bundled with OCR auth contract cleanup.

#### migrate to FastAPI

- /api/netkeiba/race
	- reason: mixed scrape + write path in Next layer; should converge to FastAPI-owned write/audit path.

#### migration in progress

- /api/netkeiba/race-list
	- status: migrated to FastAPI proxy in P1-4; keep monitoring and error telemetry before reclassifying to production.

#### experimental

- /api/netkeiba/race
- /api/netkeiba/race-list
- /api/ocr

#### deprecated candidate

- /api/netkeiba/race (after FastAPI-owned write path and caller migration)

#### internal only

- none newly designated in this P1-3 slice; classification remains as defined in section 2.

## 6. Safe Migration Steps (Next sprint)

1. Add FastAPI read-only proxy contract for race-list retrieval and switch /api/netkeiba/race-list to it.
2. Introduce FastAPI-owned write contract for scraped race persistence (single owner of DB writes).
3. Move /api/netkeiba/race write responsibility to FastAPI while keeping Next route as temporary adapter.
4. After usage confirmation, mark /api/netkeiba/race deprecated and remove direct Supabase writes from Next.
5. For OCR path, define explicit auth contract first, then migrate Supabase writes behind FastAPI endpoint.

## 7. P1-5 Write Path Decomposition (/api/netkeiba/race)

Goal in this phase:
- keep existing write behavior unchanged in Next route /api/netkeiba/race,
- add read-only preflight contract in FastAPI,
- clarify ownership before dry-run/write migration.

### 7.1 Current /api/netkeiba/race responsibility split

| Responsibility | Current Owner | Notes |
|---|---|---|
| input parsing (`raceId`, `userId`, `testOnly`) | Next API | request body contract currently defined in Next route |
| scrape service call (`/scrape/ultimate`) | Next API direct | used both in testOnly and full write path |
| Supabase write (`races`, `race_results`, `race_payouts`) | Next API direct | service-role/anon client in Next route |
| auth gate | mixed | no route-local admin/premium gate in this route |
| error mapping | Next API | 400/422/502/503/500 style mapping |

### 7.2 New preflight contract (FastAPI, read-only)

Endpoint:
- GET /api/netkeiba/race/preflight?race_id=...&date=...

Preflight guarantees:
- validates input format (race_id required, 12-digit; date optional format check),
- checks scrape service reachability,
- does not execute DB write,
- does not execute Supabase write,
- returns explicit contract for ready/degraded/unavailable.

Response shape (target contract):

```json
{
	"success": true,
	"status": "ready",
	"service": "netkeiba-race",
	"race_id": "202406010101",
	"can_scrape": true,
	"can_write": false,
	"write_performed": false,
	"reason": null
}
```

Status semantics:
- ready: scrape service reachable and race probe accepted,
- degraded: scrape service reachable but request rejected or race unavailable,
- unavailable: scrape service not reachable or upstream 5xx.

### 7.3 Migration status

| Route | riskLevel | migrationTarget | migrationStatus |
|---|---|---|---|
| /api/netkeiba/race | high | FastAPI write orchestration | preflight-added |

Phase boundary (important):
- this P1-5 step does not move write processing,
- this P1-5 step does not remove Supabase direct writes,
- write migration starts only after preflight + dry-run validation phase.

## 8. P1-6 Preflight Smoke Operational Rule

Objective:
- keep smoke check reusable for local audit and CI candidate,
- fail only when contract is broken,
- do not fail only because downstream scrape service is unavailable.

Target script:
- `scripts/smoke_netkeiba_race_preflight.py`

Output file:
- `reports/netkeiba_race_preflight_smoke_result.json`

Verdict policy:

| preflight status | smoke verdict | default CI treatment |
|---|---|---|
| ready | pass | PASS |
| degraded | warn | WARN |
| unavailable | warn | WARN/SKIP |
| contract error | fail | FAIL |

Contract error examples (must fail):
- response is not valid JSON contract,
- required keys are missing (`service`, `race_id`, `can_scrape`, `can_write`, `write_performed`),
- `can_write` is true,
- `write_performed` is true,
- invalid status value outside `ready/degraded/unavailable`.

Execution modes:
- default (`contract-only`): degraded/unavailable are warning-level and process exits success,
- strict mode (`--fail-on-nonready`): degraded/unavailable are treated as fail.

Recommended CI usage:
- use default mode for shared environment where scrape service may not be started,
- use strict mode only in environments where scrape service readiness is guaranteed.

## 9. P1-7 Dry-run Orchestration (/api/netkeiba/race/dry-run)

Goal in this phase:
- simulate FastAPI-owned write orchestration,
- keep existing Next write route unchanged,
- generate write payload preview only,
- execute no DB/Supabase write.

Endpoint:
- POST /api/netkeiba/race/dry-run

Input contract (current):
- race_id (required, 12-digit)
- date (optional, YYYYMMDD or YYYY-MM-DD)
- user_id (optional, preview-only)

Dry-run guarantees:
- `can_write=false`
- `write_performed=false`
- `dry_run=true`
- no write side effect

Response contract (target):

```json
{
	"success": true,
	"status": "ready",
	"service": "netkeiba-race",
	"race_id": "202406010101",
	"can_scrape": true,
	"can_write": false,
	"write_performed": false,
	"dry_run": true,
	"preview": {
		"tables": [
			{ "target_table": "races", "records_count": 1, "sample_records": [] },
			{ "target_table": "race_results", "records_count": 18, "sample_records": [] },
			{ "target_table": "race_payouts", "records_count": 8, "sample_records": [] }
		]
	},
	"reason": null
}
```

Status semantics:
- ready: scrape service reachable and preview successfully generated,
- degraded: scrape reachable but upstream rejection/data issue,
- unavailable: scrape service not reachable or upstream 5xx,
- invalid: request parameter format invalid.

Dry-run smoke:
- script: `scripts/smoke_netkeiba_race_dry_run.py`
- output: `reports/netkeiba_race_dry_run_smoke_result.json`
- verdict policy:
	- ready => pass
	- degraded/unavailable/invalid => warn
	- contract error => fail

Migration marker update:
- /api/netkeiba/race: `migrationStatus=dry-run-added`
- write path remains in Next route until later phase.

## 10. P1-8 Payload Contract Diff (Next write vs FastAPI dry-run preview)

Goal in this phase:
- compare payload structures before write migration,
- keep write path unchanged,
- detect structural risk early (missing fields, type drift, naming drift).

Compared targets:
- Next write payload contract (static shape from `/api/netkeiba/race`)
	- `races`
	- `race_results`
	- `race_payouts`
- FastAPI dry-run preview payload (`/api/netkeiba/race/dry-run` response preview)
	- `preview.tables[].target_table`
	- `records_count`
	- `sample_records`

Comparison script:
- `scripts/compare_netkeiba_race_payload_contract.py`

Input:
- `reports/netkeiba_race_dry_run_smoke_result.json`

Output:
- `reports/netkeiba_race_payload_contract_diff.json`

Diff categories:
- `compatible`
- `missing_in_dry_run`
- `extra_in_dry_run`
- `naming_mismatch`
- `type_mismatch`
- `unknown`

Verdict policy (initial migration phase):
- `pass`: ready + no structural diff
- `warn`: structural diff exists, or dry-run status is non-ready (`degraded/unavailable/invalid`)
- `fail`: contract broken (dry-run report missing, invalid contract flags, invalid status)

Operational stance:
- warn findings are migration backlog, not auto-migration blockers in this phase,
- fail findings indicate contract/safety break and must be fixed before write migration.

Migration marker update:
- /api/netkeiba/race: `migrationStatus=payload-diff-added`

## 11. P1-9 Guarded Write Orchestration (/api/netkeiba/race/write)

Goal in this phase:
- add FastAPI write orchestration entrypoint,
- keep default behavior non-write,
- require explicit multi-gate confirmation for any future write path,
- keep existing Next write route unchanged.

Endpoint:
- POST /api/netkeiba/race/write

Feature flag:
- `NETKEIBA_RACE_WRITE_ENABLED=false` (default)

Default behavior (flag=false):
- write is rejected,
- explicit JSON response is returned,
- `write_performed=false` is guaranteed.

Disabled response contract example:

```json
{
	"success": false,
	"status": "disabled",
	"service": "netkeiba-race-write",
	"write_performed": false,
	"reason": "NETKEIBA_RACE_WRITE_ENABLED is false"
}
```

Guard conditions (all required before future write can run):
- feature flag is true
- `confirm_write=true`
- `dry_run=false`
- valid `race_id`
- dry-run precondition status is `ready`
- preview payload is valid

P1-9 implementation boundary:
- even when all guards are satisfied, endpoint currently returns guarded no-op,
- actual write is intentionally not executed in this phase,
- `write_performed=false` remains guaranteed.

Write guard smoke:
- script: `scripts/smoke_netkeiba_race_write_guard.py`
- output: `reports/netkeiba_race_write_guard_smoke_result.json`
- verdict policy:
	- default disabled -> pass
	- blocked/guarded-noop/invalid -> warn
	- contract error -> fail

Migration marker update:
- /api/netkeiba/race: `migrationStatus=write-guard-added`

## 12. P1-10 Guarded Path Verification with Feature Flag ON

Goal in this phase:
- verify safety branches when `NETKEIBA_RACE_WRITE_ENABLED=true` in a limited local environment,
- ensure no accidental write path execution,
- keep `write_performed=false` invariant across all tested branches.

Verification scope (flag enabled process):
- confirm missing (`confirm_write=false`) => `blocked`
- dry_run mismatch (`dry_run=true`) => `blocked`
- invalid `race_id` => `invalid`
- all guards satisfied => `guarded-noop` (still no write in this phase)

Mandatory invariant:
- every branch must return `write_performed=false`
- any `write_performed=true` must be treated as FAIL

Execution model:
- use temporary process env var only (`NETKEIBA_RACE_WRITE_ENABLED=true`)
- do not persist this in `.env` or committed files

Enabled-mode smoke:
- script: `scripts/smoke_netkeiba_race_write_guard.py --expect-enabled`
- output: `reports/netkeiba_race_write_guard_enabled_smoke_result.json`

Smoke suite integration:
- optional step: `--verify-write-guard-enabled`
- default suite remains flag-off compatible.

Migration marker update:
- /api/netkeiba/race: `migrationStatus=write-guard-enabled-verified`

## 13. P1-11 Staging-only Write Guard Design (No Actual Write)

Goal in this phase:
- add staging-only double lock design before actual write implementation,
- keep production write strictly forbidden,
- keep all branches at `write_performed=false`.

Double lock (all required):
- `NETKEIBA_RACE_WRITE_ENABLED=true`
- `ALLOW_STAGING_WRITE=true`
- `APP_ENV=staging`
- request payload: `confirm_write=true`, `dry_run=false`, `payload_contract_approved=true`

Flag-only expected behavior:
- when only `NETKEIBA_RACE_WRITE_ENABLED=true`, request is still `blocked`
- `write_performed=false` remains mandatory

Hard safety checks before writer stub:
- race_id must be valid 12-digit format,
- dry-run must return `status=ready`,
- preview payload target tables must pass whitelist check (`races`, `race_results`, `race_payouts`),
- per-table records count must be within limit.

Production policy:
- if `APP_ENV=production`, response is `blocked`,
- feature flags do not override this policy.

Current boundary:
- writer is still guarded stub (`status=guarded-stub`),
- no DB write/Supabase write is executed in FastAPI,
- `write_performed=false` remains mandatory.

Safety requirements now explicit in response payload:
- backup/snapshot requirement,
- audit log requirement,
- idempotency key requirement,
- duplicate prevention requirement,
- rollback plan requirement,
- table whitelist + row count limit requirement.

Smoke coverage updates:
- default smoke: `scripts/smoke_netkeiba_race_write_guard.py`
- staging-enabled matrix: `scripts/smoke_netkeiba_race_write_guard.py --expect-enabled`
- flag-only blocked smoke: `--expect-flag-only` (optional)
- production hard-block smoke: `--expect-production-block` (optional)
- staging-lock-missing smoke: `--expect-staging-lock-missing` (optional)
- suite optional steps:
	- `--verify-write-guard-enabled`
	- `--verify-write-guard-flag-only`
	- `--verify-write-guard-production-block`
	- `--verify-write-guard-staging-lock-missing`

Migration marker update:
- /api/netkeiba/race: `migrationStatus=staging-write-guard-designed`

## 14. P1-12 Staging Writer Stub + Audit/Idempotency Design (No Write)

Goal in this phase:
- keep actual write disabled,
- define writer boundary contract for staging execution readiness,
- make idempotency/audit/limits explicit in API response.

P1-12 writer-stub design:
- writer remains no-op (`status=guarded-stub`, `write_performed=false`),
- preview payload validation includes:
	- table whitelist (`races`, `race_results`, `race_payouts`),
	- row count limits:
		- `races <= 1`
		- `race_results <= 30`
		- `race_payouts <= 100`
- non-whitelisted table or limit exceed => `blocked`.

Idempotency design (preview only):
- payload hash is generated from guarded write request + preview summary,
- idempotency key format:
	- `netkeiba_race:{race_id}:{payload_hash_prefix}`
- key is returned in response for future persistence design,
- no idempotency persistence is executed in this phase.

Audit payload preview (no persistence in this phase):
- `race_id`
- `requested_at`
- `app_env`
- `dry_run`
- `confirm_write`
- `target_tables`
- `records_count`
- `payload_hash`
- `write_performed`
- `reason`

Rollback/snapshot requirement:
- snapshot backup is required before any future actual write,
- rollback execution plan is required,
- both are returned as requirement metadata only in this phase,
- no snapshot/audit persistence is executed.

Smoke contract updates:
- `--expect-enabled` now verifies:
	- guarded-stub contract,
	- whitelist/row-limit metadata,
	- audit payload preview required fields,
	- idempotency key/payload hash format,
	- row-limit exceeded path is blocked.

Migration marker update:
- /api/netkeiba/race: `migrationStatus=staging-writer-stub-added`

## 15. P1-13 Staging Sandbox-only Write (No Production Table Write)

Goal in this phase:
- allow first actual write only to sandbox tables in staging,
- keep production and base tables protected,
- keep default flows non-write.

Sandbox write scope:
- target mode must be explicit:
	- `sandbox_write=true`
	- `target_mode=sandbox`
- required locks remain:
	- `NETKEIBA_RACE_WRITE_ENABLED=true`
	- `ALLOW_STAGING_WRITE=true`
	- `APP_ENV=staging`
	- `confirm_write=true`
	- `dry_run=false`
	- valid `race_id`
	- `payload_contract_approved=true`
	- `idempotency_key` present

Allowed write destination in this phase:
- `sandbox_netkeiba_races`
- `sandbox_netkeiba_race_results`
- `sandbox_netkeiba_race_payouts`

Forbidden destination:
- base tables (`races`, `race_results`, `race_payouts`) are not used for actual write.

Safety behavior:
- if sandbox tables are missing -> `status=stopped` (warn) with explicit table list,
- if row limit exceeds -> `blocked`,
- if whitelist mismatch -> `blocked`,
- if production -> always `blocked`.

Write result semantics:
- `sandbox-written` only when sandbox write succeeds,
- only this case allows `write_performed=true`,
- response includes:
	- `target_mode=sandbox`
	- target table list
	- records written
	- idempotency key
	- audit payload (preview data)

Operational policy:
- default smoke/suite does not run sandbox write,
- sandbox write check runs only with explicit option:
	- `scripts/smoke_netkeiba_race_write_guard.py --expect-sandbox-write`
	- suite optional: `--verify-write-guard-sandbox-write`

Migration marker update:
- /api/netkeiba/race: `migrationStatus=sandbox-write-added`

## 16. P1-14 Sandbox Table Existence + Schema Compatibility Precheck (Read-only)

Goal in this phase:
- validate sandbox write readiness without any write/readback,
- verify sandbox table existence and schema compatibility,
- keep write safety contracts strict.

Precheck endpoint:
- `GET /api/netkeiba/race/sandbox/precheck`

Expected sandbox tables (only):
- `sandbox_netkeiba_races`
- `sandbox_netkeiba_race_results`
- `sandbox_netkeiba_race_payouts`

Precheck checks (read-only only):
- table existence,
- required columns (`race_id` and `data|payload`),
- text-type compatibility for required columns,
- row-limit support metadata (`races<=1`, `race_results<=30`, `race_payouts<=100`),
- base table reference detection in sandbox table SQL objects.

Precheck response contract:
- `service=netkeiba-race-sandbox-precheck`
- `target_mode=sandbox`
- `write_performed=false` (fixed)
- `status=ready|stopped|warn|unavailable`
- table-level report with:
	- `exists`
	- `schema_compatible`
	- `missing_columns`
	- `type_mismatches`
	- `row_limit_supported`
	- `references_base_tables`

Safety behavior:
- base tables (`races`, `race_results`, `race_payouts`) are not precheck targets,
- missing sandbox table is not hard fail (`stopped`/`warn` contract),
- contract violation only is treated as fail in smoke.

Smoke/suite updates:
- new smoke mode:
	- `scripts/smoke_netkeiba_race_write_guard.py --expect-sandbox-precheck`
- suite optional precheck step:
	- `--verify-write-guard-sandbox-precheck`
- both are explicit opt-in and remain excluded from default suite.

Migration marker update:
- /api/netkeiba/race: `migrationStatus=sandbox-precheck-added`

## 17. P1-14.5 Sandbox Table DDL / Migration Plan (Manual-only)

Goal in this phase:
- keep sandbox precheck read-only,
- define sandbox table schema explicitly,
- avoid write/readback expansion until precheck becomes `ready`.

DDL artifact:
- `docs/migrations/netkeiba_sandbox_tables.sql`

Target tables (sandbox only):
- `sandbox_netkeiba_races`
- `sandbox_netkeiba_race_results`
- `sandbox_netkeiba_race_payouts`

Required columns aligned with precheck:
- `race_id`
- `data` or `payload`
- `created_at`
- `idempotency_key`
- `payload_hash`
- `audit_payload`

Migration safety policy:
- manual apply only (no auto-apply endpoint/script added),
- production/base tables are not altered,
- no Supabase write is involved,
- no production write permission is changed.

Rollback/drop plan:
- included in SQL file as manual drop sequence,
- drops sandbox tables only in reverse dependency-safe order.

Precheck `ready` condition (for next phase gate):
- all 3 sandbox tables exist,
- required columns are present,
- type compatibility check passes,
- base-table reference scan is clean.

Operational next-step gate:
- while precheck is `stopped` or `warn`, do not start write/readback implementation.
- proceed to P1-15 only after precheck is consistently `ready`.

## 18. P1-15 Manual DDL Apply + Precheck Ready Verification (No Write)

Goal in this phase:
- apply sandbox DDL manually in sandbox/staging DB,
- verify precheck transitions to `ready/pass`,
- stop before sandbox write/readback.

Executed in this phase:
- manual DDL apply from `docs/migrations/netkeiba_sandbox_tables.sql`,
- FastAPI restart,
- smoke verification:
	- `python scripts/smoke_netkeiba_race_write_guard.py --expect-sandbox-precheck`
	- result: `verdict=pass`, `verdict_reason=sandbox-precheck-ready`.

Safety confirmation:
- no sandbox write execution,
- no readback implementation,
- no production/base table DDL update,
- default suite remained non-write (`python scripts/run_keiba_smoke_suite.py`).

Gate status:
- precheck readiness gate is satisfied for next phase planning,
- write/readback remains intentionally out of scope for this phase.

## 19. P1-16 Sandbox Write + Readback Verification (Sandbox Only)

Goal in this phase:
- execute actual write only to sandbox tables,
- immediately verify written rows by sandbox readback,
- keep production/base tables strictly out of write/readback scope.

Implemented in this step:
- `/api/netkeiba/race/write` sandbox path now performs readback verification after successful sandbox write.
- readback scope is limited to:
	- `sandbox_netkeiba_races`
	- `sandbox_netkeiba_race_results`
	- `sandbox_netkeiba_race_payouts`
- readback key uses `race_id + idempotency_key` and verifies:
	- `records_written` and readback count match,
	- target tables are sandbox-only,
	- `idempotency_key` match,
	- `payload_hash` match,
	- `audit_payload` presence.

Mismatch behavior:
- explicit response status: `sandbox-readback-mismatch`
- reason: readback mismatch/failure detail
- no fallback to production/base table readback.

Safety behavior preserved:
- precheck `ready` remains mandatory before any sandbox write,
- `idempotency_key` required for sandbox write,
- row-limit/table whitelist violations remain write-blocking,
- default smoke/default suite remain non-write,
- sandbox write-readback runs only with explicit opt-in flags.

Migration marker update:
- /api/netkeiba/race: `migrationStatus=sandbox-write-readback-added`

## 20. UI Frontend Integration Final State

Updated: 2026-07-05

Completion scope considered done:
- UI screens calling Next API routes,
- Next API to FastAPI routing contracts,
- Premium/Admin UI guard behavior,
- dedicated health check contracts,
- route classification and direct-path inventory,
- smoke suite coverage for default non-write flows,
- lint/build verification stability.

Explicitly separate from UI integration completion:
- P1-16 sandbox write-readback runtime actual pass,
- upstream scrape service ready verification,
- develop merge decision for sandbox write-readback PR.

Safety note:
- default UI/API flows do not perform sandbox write or production/base-table write,
- runtime write-readback remains an explicit opt-in data-migration verification step.
