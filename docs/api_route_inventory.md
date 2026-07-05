# API Route Inventory and Classification

Updated: 2026-07-05 (P1-5 preflight)
Scope: Next.js API routes and FastAPI endpoints classification

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
