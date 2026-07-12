# Python Production Usage Map (Canonicalized for Phase 3A)

## Provenance
- recovered source path: `C:/Users/yuki2/Documents/ws/keiba-ai-pro/docs/python_production_usage_map.md`
- source blob hash: `e2ab94114414f023bded0c863ea6c54634abbbd9`
- reconciled base SHA (`origin/develop`): `80556e8ca2fae2280a0d2f5913ed14068d248d8e`
- reconciliation date: `2026-07-12`
- note: This map distinguishes **online runtime imports**, **Next-routed script execution**, and **ops/offline paths**.

---

## 1. Classification Rules
A Python file is classified as production-relevant when it is one of:
1. Imported by FastAPI process reachable from production runtime.
2. Executed by Next API routes via allowlisted child process execution.
3. Invoked by CI/ops flows that are part of production readiness controls.

---

## 2. Online Runtime (Always-on FastAPI)

### Entry/core
- `python-api/main.py`
- `python-api/app_config.py`
- `python-api/models.py`
- `python-api/scheduler.py`
- `python-api/middleware/auth.py`
- `python-api/deps/auth.py`
- `python-api/deps/pred_limit.py`

### Included routers (from `main.py`)
- `python-api/routers/stats.py`
- `python-api/routers/train.py`
- `python-api/routers/predict.py`
- `python-api/routers/models_mgmt.py`
- `python-api/routers/purchase.py`
- `python-api/routers/scrape.py`
- `python-api/routers/backfill.py`
- `python-api/routers/export.py`
- `python-api/routers/profiling.py`
- `python-api/routers/races.py`
- `python-api/routers/internal.py`
- `python-api/routers/debug_data.py`
- `python-api/routers/realtime_odds.py`
- `python-api/routers/bet_export.py`
- `python-api/routers/feature_analysis.py`
- `python-api/routers/prediction_history.py`

### Scrape subsystem
- `python-api/scraping/constants.py`
- `python-api/scraping/fetch_pipeline.py`
- `python-api/scraping/horse.py`
- `python-api/scraping/race.py`
- `python-api/scraping/jobs.py`
- `python-api/scraping/storage.py`

---

## 3. Python Executed via Next API Routes

### Scrape planning routes
- `scripts/plan_scrape_refresh.py`
  - called by `src/app/api/scrape/refresh-plan/route.ts`
- `scripts/plan_p0_scrape_repair.py`
  - called by `src/app/api/scrape/p0-repair-plan/route.ts`

### Production readiness route allowlist
- `scripts/smoke_analyze_race_api.py`
- `scripts/run_keiba_smoke_suite.py`
- plus python compile check command (`python -m compileall`) executed by route allowlist.

---

## 4. Ops / Batch Paths (Not Online Request Path)
- `python-api/run_scrape.py` (scheduled/batch usage path)
- CI-invoked Python checks in `.github/workflows/ci.yml`:
  - pytest suites
  - static/contract/security checks

---

## 5. Not Always Production-Online
These can still be operationally important, but are not always-on runtime imports:
1. `python-api/tests/**`
2. `python-api/training/**` (training phase)
3. large portion of `scripts/**` diagnostics and local tooling

---

## 6. Reconciliation Notes
- Source map intent preserved.
- Updated to current `main.py` router include list.
- Route-linked script execution is aligned with current Next route implementations.
- Script reference drift corrected where necessary (current repo does not contain `scripts/diagnose_source_empty_result_cells.py`).
