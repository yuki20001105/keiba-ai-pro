# API Route Inventory and Classification

Updated: 2026-07-05
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
| /api/netkeiba/race-list | external scrape service | utility/integration route |
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
