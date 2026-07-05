# Frontend UI / Backend Contract Inventory

Updated: 2026-07-05
Scope: Current-state inventory only (no behavior change)

## 0. Purpose

This document inventories how Frontend UI calls Next.js API routes, how those routes map to FastAPI endpoints, and where design gaps exist.

Architecture (current):
- Primary path: Frontend UI -> Next.js API Route -> FastAPI -> DB/Model/Scrape/Supabase
- Mixed path (partial): Frontend/Next API -> external scrape service and/or direct Supabase write

## 1. UI Page Inventory

| UI Page | Role | Uses API | Notes |
|---|---|---|---|
| src/app/home/page.tsx | entry dashboard | yes | health/data summary |
| src/app/data-collection/page.tsx | scrape/profiling ops | yes | includes scrape health-like probe |
| src/app/train/page.tsx | model training management | yes | async train job flow |
| src/app/predict-batch/page.tsx | batch race prediction/purchase | yes | central prediction workflow |
| src/app/race-analysis/page.tsx | single race analysis | yes | model switch + cache |
| src/app/feature-lab/page.tsx | feature catalog/importance/coverage | yes | premium-sensitive endpoints behind API |
| src/app/data-view/page.tsx | raw/features debug viewer | yes | premium-sensitive debug endpoints |
| src/app/prediction-history/page.tsx | prediction performance history | yes | premium-sensitive endpoint |
| src/app/dashboard/page.tsx | purchase performance dashboard | yes | purchase/statistics endpoints |
| src/app/admin/page.tsx | admin operational page | yes | currently uses data-stats |
| src/app/login/page.tsx | authentication screen | no direct backend | auth entry only |
| src/app/page.tsx | landing page | no direct backend | marketing/entry |

## 2. UI -> Next API -> FastAPI Mapping (Core)

| UI Screen | Next API Route | FastAPI Endpoint | Permission (effective) | State | Gap |
|---|---|---|---|---|---|
| train | /api/ml/train/start | POST /api/train/start | premium required on backend for /api/train, unclear for /api/train/start parity | production | permission consistency should be explicit |
| train | /api/ml/train/status/[job_id] | GET /api/train/status/{job_id} | login required (token expected) | production | add UI pre-check for unauthorized |
| train | /api/models | GET /api/models | login required | production | none critical |
| train | /api/models/[id] | GET/DELETE /api/models/{model_id} | login required | production | none critical |
| train | /api/models/[id]/activate | PUT /api/models/{model_id}/activate | login required/admin-intent | production | role policy should be explicit in UI and backend |
| predict-batch | /api/analyze-race | POST /api/analyze_race | login required | production | none critical |
| predict-batch | /api/races/by-date | GET /api/races/by_date | login required | production | naming mixed (by-date vs by_date) |
| predict-batch | /api/realtime-odds/[race_id] | GET /api/realtime-odds/{race_id} | login required | production | none critical |
| predict-batch | /api/realtime-odds/refresh | POST /api/realtime-odds/refresh | login required | production | none critical |
| predict-batch | /api/export/bet-list | POST /api/export/bet-list and /csv | login required | production | none critical |
| predict-batch | /api/purchase | POST /api/purchase | login required | production | none critical |
| race-analysis | /api/prediction-history/[race_id] | GET /api/prediction-history/{race_id} | premium required (backend) | production | UI guard missing (403 discovered late) |
| race-analysis | /api/debug/race/[race_id]/features | GET /api/debug/race/{race_id}/features | premium required (backend) | production | UI guard missing |
| data-view | /api/debug/race/[race_id] | GET /api/debug/race/{race_id} | premium required (backend) | production | UI guard missing |
| data-view | /api/debug/race/[race_id]/features | GET /api/debug/race/{race_id}/features | premium required (backend) | production | UI guard missing |
| prediction-history | /api/prediction-history | GET /api/prediction-history | premium required (backend) | production | UI guard missing |
| data-collection | /api/scrape | POST /api/scrape (legacy sync) | login required/admin-intent | production | uses legacy sync endpoint |
| data-collection | /api/scrape/status/[jobId] | GET /api/scrape/status/{job_id} | login required | production | batch scrape polling endpoint |
| data-collection | /api/scrape/health | GET /api/scrape/health | login required | production | dedicated health contract |
| data-collection | /api/profiling | POST /api/profiling/start | login required | production | query/body contract should be documented |
| data-collection | /api/profiling/status/[job_id] | GET /api/profiling/status/{job_id} | login required | production | none critical |
| data-collection | /api/races/recent | GET /api/races/recent | login required | production | none critical |
| data-collection | /api/races/[race_id]/horses | GET /api/races/{race_id}/horses | login required | production | none critical |
| dashboard | /api/purchase/[id] | PATCH/DELETE /api/purchase/{purchase_id} | login required | production | none critical |
| dashboard | /api/purchase-history | GET /api/purchase_history | login required | production | naming mixed |
| dashboard | /api/statistics | GET /api/statistics | login required | production | none critical |
| feature-lab | /api/features/summary | GET /api/features/summary | login required/premium-intent | production | UI should show role requirement |
| feature-lab | /api/features/importance | GET /api/features/importance | login required/premium-intent | production | none critical |
| feature-lab | /api/features/coverage | GET /api/features/coverage | login required/premium-intent | production | none critical |

## 3. Permission Guard Inventory

| Route or Screen | Backend Guard | UI Guard | Current Risk |
|---|---|---|---|
| GET /api/prediction-history and /{race_id} | require_premium | no explicit pre-guard | user sees runtime failure instead of gated UX |
| GET /api/debug/race/{race_id} and /features | require_premium | no explicit pre-guard | premium feature exposed by navigation but denied at runtime |
| POST /api/train (synchronous route) | require_premium | no explicit pre-guard | unexpected 403 if called directly |
| POST /api/scrape/start and POST /api/scrape/repair/{race_id} | require_admin | no explicit pre-guard | operational actions rely on backend-only reject |
| screens using authFetch generally | token optional at fetch layer | no centralized role gating matrix | inconsistent UX across pages |

## 4. Unused/Holding API Route Inventory (Current UI)

Classification policy used here:
- production: actively used by page/hook flows
- experimental: present but not currently consumed by page/hook flows
- internal: backend-internal orchestration path
- deprecated: old path kept for compatibility, replacement exists

| Next API Route | Usage from current UI pages/hooks | Classification | Reason |
|---|---|---|---|
| src/app/api/ai-correct/route.ts | not observed | experimental | not mapped to active page flow |
| src/app/api/ocr/route.ts | not observed | experimental | not mapped to active page flow |
| src/app/api/netkeiba/calendar/route.ts | not observed | experimental | external scrape utility path |
| src/app/api/netkeiba/race-list/route.ts | not observed | experimental | external scrape utility path |
| src/app/api/netkeiba/race/route.ts | not observed | experimental | direct scrape+Supabase write path |
| src/app/api/stripe/create-checkout/route.ts | not observed | experimental | billing flow not linked from current pages |
| src/app/api/stripe/portal/route.ts | not observed | experimental | billing flow not linked from current pages |
| src/app/api/stripe/webhook/route.ts | webhook only | internal | external callback entry, not UI-called |
| src/app/api/data/all/route.ts | not observed | internal | destructive admin utility |
| src/app/api/export/data/route.ts | not observed | internal | ops/export utility |
| src/app/api/export/db/route.ts | not observed | internal | ops/export utility |
| src/app/api/debug/race-ids/route.ts | not observed | internal | diagnostics utility |
| POST /api/scrape via src/app/api/scrape/route.ts | used by hooks | deprecated candidate | /api/scrape/start exists and is preferred async design |

## 5. Naming Convention Gap Inventory

| Layer A | Layer B | Example | Gap |
|---|---|---|---|
| Next route (kebab) | FastAPI endpoint (snake) | /api/data-stats -> /api/data_stats | translation required, prone to omissions |
| Next route (kebab) | FastAPI endpoint (snake) | /api/races/by-date -> /api/races/by_date | same |
| Next route (kebab) | FastAPI endpoint (snake) | /api/analyze-race -> /api/analyze_race | same |
| Next route (kebab) | FastAPI endpoint (snake) | /api/purchase-history -> /api/purchase_history | same |
| Next route group naming | backend semantics | /api/ml/train/* vs /api/train/* | overlapping naming domains |
| job id param naming | backend param naming | [jobId] vs {job_id} | minor but recurring mismatch |

## 6. Health-Check and Runtime Probe Inventory

| Contract | Endpoint Chain | Response | Notes |
|---|---|---|---|
| scrape health check | UI -> /api/scrape/health -> FastAPI /api/scrape/health | { success, status, service, timestamp, reason? } | status: healthy / degraded / unhealthy / unknown |
| app health check | UI -> /api/health -> FastAPI /health | existing app-level contract | keep as top-level service heartbeat |

Current policy:
- use dedicated health endpoints for service readiness/liveness checks,
- do not overload business status APIs as health probes.

## 7. Mixed Path Inventory (Bypass of FastAPI-Centric Path)

| Path | Current Route | Risk |
|---|---|---|
| Next API -> external scrape service -> direct Supabase write | src/app/api/netkeiba/race/route.ts | logic split, schema drift risk, harder observability |
| Next API -> external scrape utility endpoints | src/app/api/netkeiba/* | duplicated integration surface |

Design direction:
- converge write operations to FastAPI as system of record,
- keep Next API thin as auth/session proxy where needed.

## 8. Improvement Priority Table

| Priority | Theme | Action | Expected Effect |
|---|---|---|---|
| P0 | Permission UX | Add explicit Premium/Admin UI guard and role-aware messages on feature-lab, data-view, prediction-history, admin ops | prevent late runtime authorization failures |
| P0 | Contract visibility | Keep this inventory updated as canonical ledger and require update on API/screen changes | reduce onboarding and regression risk |
| P1 | Health consistency | Add dedicated scrape health endpoint and replace fake job-id probe | stable service readiness semantics |
| P1 | Route lifecycle clarity | Mark Next API routes with production/experimental/internal/deprecated labels | prevent accidental dependency on provisional routes |
| P2 | Path consolidation | Reduce direct external scrape/Supabase write path and route through FastAPI | stronger consistency and auditability |
| P2 | Naming consistency | Define one canonical naming policy and migration plan (kebab vs snake at boundaries) | lower maintenance overhead |

## 9. Suggested Next Sprint Execution Order

1. Add role-based UI pre-guard for premium/admin screens.
2. Introduce dedicated health check contract for scrape subsystem.
3. Add route lifecycle labels and README/docs references.
4. Plan migration from mixed path (direct external/supabase) to FastAPI-centric path.
5. Start naming standardization plan with compatibility layer.

## 10. Notes and Constraints

- This document is inventory-only; no runtime behavior changes included.
- No secrets/tokens are documented.
- No DB, Supabase, or API contract mutation is performed by this change.

## 11. Implementation Status (P0 UI Guard)

Updated: 2026-07-05

Implemented in frontend:
- Premium guard context fields added:
	- role, subscription_tier, isPremium, isAdmin (UI-side pre-check)
- Common guard UI components added:
	- src/components/LockedFeatureCard.tsx
	- src/components/PremiumRequiredNotice.tsx
	- src/components/AdminRequiredNotice.tsx

Guarded screens (P0):
- src/app/prediction-history/page.tsx
	- Premium badge shown
	- non-premium: refresh disabled, API not called, notice shown
	- 401/403: explicit permission message
- src/app/data-view/page.tsx
	- Premium badge shown
	- non-premium: race detail buttons disabled, debug APIs not called, notice shown
	- 401/403: explicit permission message
- src/app/feature-lab/page.tsx
	- Premium badge shown
	- non-premium: tab/target actions disabled, feature APIs not called, notice shown
	- 401/403: explicit permission message
- src/app/race-analysis/page.tsx
	- non-premium: premium tabs (features/result) disabled
	- non-premium: debug/features and prediction-history calls suppressed
	- premium-only notices shown in locked tabs
	- 401/403 on result API: explicit permission message
- src/app/admin/page.tsx
	- Admin badge shown in header

Notes:
- Backend authorization remains authoritative; UI guard is pre-check UX only.
- API contracts and backend permission logic are unchanged.

## 12. Verification Baseline (P0.5)

Updated: 2026-07-05

Status:
- `npm run build`: pass
- `npm run lint`: runs successfully (warnings-only baseline)

Changes made for verification stability:
- lint script migrated from `next lint` to ESLint CLI (Next 16 compatible)
- flat config introduced via `eslint-config-next/core-web-vitals` + `eslint-config-next/typescript`
- lint target narrowed to frontend/source and skill verification TS files to avoid scanning embedded Python environments
- `.ts` import extension issues fixed in skill verification scripts and aggregator script
- `/race-analysis` build blocker fixed by wrapping `useSearchParams()` usage in a Suspense boundary

Known remaining debt (non-blocking):
- current lint output includes warnings (unused vars, exhaustive-deps, unused-expressions) across existing files
- warnings are preserved intentionally to avoid broad refactor in this sprint

## 13. Implementation Status (P1 Health Check Contract)

Updated: 2026-07-05

Implemented:
- FastAPI dedicated endpoint added: `GET /api/scrape/health`
- Next.js proxy route added: `GET /api/scrape/health`
- data-collection UI switched from fake job-id probe to health contract

Health response contract:
- `success`: boolean
- `status`: `healthy | degraded | unhealthy | unknown`
- `service`: `scrape`
- `timestamp`: ISO8601 string
- `reason`: optional string

Notes:
- Existing scrape job API (`/api/scrape/status/{job_id}`) remains unchanged for polling.
- Health check is read-only; no DB mutation.

## 14. Route Classification Reference (P1-2)

Updated: 2026-07-05

Detailed inventory and classification moved to:
- `docs/api_route_inventory.md`

Next API lightweight metadata source:
- `src/app/api/route-classification.ts`

Policy:
- Do not delete routes in the same sprint as classification.
- Mark deprecated/unused first, then remove only after caller migration is complete.

## 15. Implementation Status (P1-3 Direct Path Inventory)

Updated: 2026-07-05

Implemented in this step:
- Direct dependency inventory added for:
	- Supabase direct write routes
	- Scrape service direct call routes
- Responsibility split documented (auth, DB write, log/audit, failure handling)
- Migration buckets documented:
	- keep for now
	- migrate to FastAPI
	- experimental
	- deprecated candidate
	- internal only
- Route metadata extended with direct dependency and risk fields

Reference:
- `docs/api_route_inventory.md` section 5 and section 6
- `src/app/api/route-classification.ts`

Constraints preserved:
- No existing route behavior changed.
- No DB write path removed.
- No auth policy relaxed.
