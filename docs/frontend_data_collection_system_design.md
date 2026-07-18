# Frontend Data Collection System Design (Canonicalized for Phase 3A)

## Provenance
- recovered source path: `C:/Users/yuki2/Documents/ws/keiba-ai-pro/docs/frontend_data_collection_system_design.md`
- source blob hash: `0c53f582b6e26390f04561d31e1c4aa5e91290db`
- reconciled base SHA (`origin/develop`): `80556e8ca2fae2280a0d2f5913ed14068d248d8e`
- reconciliation date: `2026-07-12`
- note: This document separates **implemented (as-is)** from **planned (future)** explicitly.

---

## 1. Scope
This document covers the Data Collection frontend and adjacent read-only planning surfaces.

- UI pages:
  - `src/app/data-collection/page.tsx`
  - `src/app/data-collection/refresh-plan/page.tsx`
  - `src/app/data-collection/p0-repair-plan/page.tsx`
  - `src/app/data-collection/targeted-refetch-plan/page.tsx`
  - `src/app/data-collection/live-validation/page.tsx`
- Next API routes:
  - `src/app/api/scrape/*`
  - `src/app/api/data-stats/route.ts`
  - `src/app/api/races/*`
- FastAPI scrape routes:
  - `python-api/routers/scrape.py`
- Related scripts (verified existing):
  - `scripts/plan_scrape_refresh.py`
  - `scripts/plan_p0_scrape_repair.py`
  - `scripts/plan_p0_targeted_refetch.py`
  - `scripts/validate_p0_targeted_refetch_live.py`
  - `scripts/plan_p0_reparse_cache.py`
  - `scripts/diagnose_p0_cache_coverage.py`

Observed mismatch from source document:
- `scripts/diagnose_source_empty_result_cells.py` is not present in current `develop` implementation.

---

## 2. Implemented (As-Is)

### 2.1 Data Collection main page
- Dry-run trigger exists and posts through Next route `/api/scrape` with `dry_run: true`.
- Job polling exists via `/api/scrape/status/{jobId}`.
- Execute path exists via `useBatchScrape()` integration.
- Fetch summary history exists via `/api/scrape/history`.
- Health status probe exists via `/api/scrape/health`.
- Stats and recent races integration exists via `/api/data-stats`, `/api/races/recent`, `/api/races/{race_id}/horses`.
- Links to Refresh Plan and P0 Repair Plan exist from the Data Collection context.

### 2.2 Refresh Plan page and route
- UI is preview-oriented and clearly indicates dry-run intent.
- Next route `src/app/api/scrape/refresh-plan/route.ts`:
  - Authz enforced with `verifyRequestAuth(...requirePremiumOrAdmin...)`.
  - Runs `scripts/plan_scrape_refresh.py` in a child process.
  - Returns `dry_run: true`, `update_enabled: false`.
  - `PUT` responds `501 not-implemented`.
  - Path-like unsafe input keys are rejected (`FORBIDDEN_PATH_KEYS`).

### 2.3 P0 Repair Plan page and route
- UI is preview-only; execute buttons are disabled.
- Next route `src/app/api/scrape/p0-repair-plan/route.ts`:
  - Premium/Admin authz enforced.
  - Runs `scripts/plan_p0_scrape_repair.py`.
  - Returns `dry_run: true`, `read_only: true`, `update_enabled: false`.
  - `PUT` responds `501 not-implemented`.

### 2.4 Targeted Refetch Plan page and route
- UI is preview-only and does not expose execution controls.
- Next route `src/app/api/scrape/targeted-refetch-plan/route.ts`:
  - Authz enforced with `verifyRequestAuth(...requirePremiumOrAdmin...)`.
  - Runs `scripts/plan_p0_targeted_refetch.py`.
  - Returns `dry_run: true`, `read_only: true`, `execution_enabled: false`.
  - Applies fail-closed report validation for numeric fields, URL samples, and safety flags.
  - Rejects unknown/path-like inputs and strips server filesystem paths from responses.

### 2.5 Bounded Live Validation page and route
- UI requires an explicit confirmation before any external HTTP request.
- UI caps validation at 3 sequential URLs and exposes pass/warn/partial/error/busy as separate states.
- Each selected URL permits one outbound attempt with no automatic retry, so one run performs at most 3 external HTTP requests.
- Next route `src/app/api/scrape/live-validation/route.ts`:
  - re-verifies Admin authorization;
  - accepts only `target`, `url_type`, `max_urls`, and literal `confirm_live_fetch=true`;
  - forwards the verified bearer token to FastAPI;
  - validates and allowlist-projects the response;
  - never starts Python and never accepts a URL or filesystem path.
- FastAPI route `POST /api/scrape/live-validation`:
  - independently enforces Admin authorization;
  - runs fixed server-owned planner/validator commands with shell disabled;
  - disables redirects and bounds timeout, body size, output size, concurrency, cooldown and total runtime;
  - treats only non-expired cache rows as available in both planning and validation;
  - requires response-derived horse identity and real pedigree evidence instead of trusting request URLs as parse evidence;
  - recomputes result counts and verdict from validated sample rows before returning evidence;
  - performs no DB repair/upsert/cache write and removes temporary reports on all paths.
- Runtime prerequisites are server-owned report inputs plus read-only data/cache volumes at the documented container paths. Reports mount at `/app/keiba/data/live-validation-inputs:ro`; operational data remains under `/app/keiba/data`. Neither is bundled into the image or Next/Vercel deployment, and missing prerequisites fail closed before external HTTP.

### 2.6 FastAPI scrape contracts relevant to frontend
- `POST /api/scrape/start`: starts async scrape job.
- `GET /api/scrape/status/{job_id}`: job status/progress/result.
- `GET /api/scrape/history`: recent jobs.
- `GET /api/scrape/health`: scrape health contract.
- Legacy/specialized paths remain available (not all wired by UI):
  - `POST /api/scrape`
  - `POST /api/rescrape_incomplete`

---

## 3. Planned (Future)
- Controlled staging evidence for the bounded live-validation UI and FastAPI service.
- Unified operational dashboard that joins:
  - refresh dry-run
  - p0 repair dry-run
  - targeted refetch dry-run
  - cache/reparse diagnostics
- Controlled, approval-gated execution phase for refresh/p0 repair (currently intentionally disabled).

---

## 4. API Contract Matrix (As-Is)

| frontend screen | Next route | backend/script | method | read-only | external HTTP | DB write | status |
|---|---|---|---|---|---|---|---|
| Data Collection | `/api/scrape` | FastAPI `/api/scrape/start` | POST | dry-run yes / execute no | dry-run: no, execute: yes | dry-run: no, execute: yes | implemented |
| Data Collection | `/api/scrape/status/{jobId}` | FastAPI `/api/scrape/status/{job_id}` | GET | yes | no | no | implemented |
| Data Collection | `/api/scrape/history` | FastAPI `/api/scrape/history` | GET | yes | no | no | implemented |
| Data Collection | `/api/scrape/health` | FastAPI `/api/scrape/health` | GET | yes | no | no | implemented |
| Refresh Plan | `/api/scrape/refresh-plan` | `plan_scrape_refresh.py` | POST/GET | yes | no | no | implemented |
| Refresh Plan execute | `/api/scrape/refresh-plan` | none | PUT | yes | no | no | disabled (`501`) |
| P0 Repair Plan | `/api/scrape/p0-repair-plan` | `plan_p0_scrape_repair.py` | POST/GET | yes | no | no | implemented |
| P0 Repair execute | `/api/scrape/p0-repair-plan` | none | PUT | yes | no | no | disabled (`501`) |
| Targeted Refetch Plan | `/api/scrape/targeted-refetch-plan` | `plan_p0_targeted_refetch.py` | POST | yes | no | no | implemented |
| Bounded Live Validation | `/api/scrape/live-validation` | FastAPI `/api/scrape/live-validation` + fixed planner/validator | POST | yes | yes (max 3, sequential) | no | implemented, L2 contract-ready |

---

## 5. Reconciliation Notes
- Source document intent is preserved.
- All implementation claims were rewritten against current `develop` code.
- Non-existent script reference was corrected to currently existing planning/diagnostic scripts.
- Execution-vs-plan boundary is now explicit to prevent misreading preview UI as write-enabled behavior.
