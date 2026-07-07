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
| data-collection | /api/scrape/history | GET /api/scrape/history | login required | production | fetch summary history (read-only) |
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

## 22. Implementation Status (P0 Production Readiness Check)

Updated: 2026-07-05

Implemented:
- New UI page: `src/app/production-readiness/page.tsx`
- New Next API route: `src/app/api/production-readiness/route.ts`

Purpose:
- Provide pre-production checks for prediction/analysis/read-only operations from UI.
- Explicitly exclude write operations (production/base write and sandbox write-readback).

UI contract:
- Card-based checklist with `pass | warn | fail | unknown`.
- Single execution button with loading state.
- Error summary displayed in-page.
- Minimal JSON details only (no secret value exposure).
- Premium/Admin guard on execution button.

API contract (`POST /api/production-readiness`):
- Executes only allowlisted checks:
	- frontend build (`npm run build`)
	- python compileall
	- FastAPI health (`/health`)
	- scrape health (`/api/scrape/health`)
	- analyze_race smoke script
	- smoke suite script (summary read)
	- secret scan (`git grep -n -I "<notion-token-prefix>"`)
	- git status notice parsing
	- env flag checks (`APP_ENV`, write flags)
- Returns aggregated checklist with per-item state and summary.

Safety constraints:
- No call to production/base write endpoints.
- No sandbox write-readback execution in normal readiness check.
- No `.env` value dump in response.
- No service_role key operations are introduced.

P0.5 auth-aware smoke handling:
- `KEIBA_AUTH_BEARER_TOKEN` 未設定で認証必須 endpoint が `401/403` を返した場合、readiness は `auth-required` として `warn` 分類。
- `KEIBA_AUTH_BEARER_TOKEN` 設定時は同 endpoint を通常の pass/fail 判定で評価。
- readiness/smoke の要約にはトークン実値を含めない（presence のみ扱う）。

## 23. Implementation Status (P2 Notion Report Output UI)

Updated: 2026-07-05

Implemented:
- New UI page: `src/app/notion-report/page.tsx`
- New Next API route: `src/app/api/notion-report/route.ts`

Purpose:
- Promote Notion export from script-only to Premium/Admin UI flow.
- Support `preview -> send` while keeping Notion secrets server-side only.

UI contract:
- Report type selector (fixed allowlist):
	- `feature analysis`
	- `smoke suite summary`
	- `production readiness summary`
	- `model evaluation summary`
- Preview generation button.
- Notion send button (enabled after preview).
- Result state badge (`pass | warn | fail`) and code display.
- Config missing state (`config-missing`) shown as `warn`.

API contract (`/api/notion-report`):
- `GET`: returns report type list and Notion config status.
- `POST` with `action=preview|send` and allowlisted `reportType` only.
- No arbitrary file path parameter is accepted.
- Server-side auth guard:
	- non-authenticated: `401`
	- non-Premium/Admin: `403`

Security constraints:
- `NOTION_TOKEN` and `NOTION_PARENT_PAGE_ID` are read only on server-side.
- Token values are never returned in JSON and never rendered in UI.
- Error strings are sanitized before response.
- No `service_role` key is used in this route.
- Read-only policy is preserved (no production/base table write).

## 24. Implementation Status (P1 Model Redesign Workbench MVP)

Updated: 2026-07-06

Implemented:
- New UI page: `src/app/model-redesign-workbench/page.tsx`
- New Next API route: `src/app/api/model-redesign/summary/route.ts`

Purpose:
- Provide a read-only / preview-first Model Redesign Workbench MVP.
- Expose active-model snapshot, evaluation metrics, feature warnings, and improvement preview without retrain execution.

UI contract:
- Premium/Admin guard on view.
- Sections:
	- active model summary
	- metrics (RMSE / AUC / Spearman / 的中率 / ROI)
	- feature importance
	- correlation / duplicate warnings
	- removal candidates
	- improvement preview
- retrain and active-model-switch controls are disabled (`not-implemented`).
- link to Notion output UI (`/notion-report`).

API contract (`/api/model-redesign/summary`):
- Server-side Bearer auth + Premium/Admin role check.
- Read-only allowlist sources only:
	- `python-api/models/.active_model.json`
	- `docs/reports/feature_analysis.json`
	- latest `docs/reports/iter_*_metrics.json`
	- `reports/roi_report.csv` (optional)
- Path-like inputs are forbidden (`path`, `filePath`, `reportPath`, `modelPath`, `sourcePath`).
- Missing data is returned as `warn` (`config-missing` or `data-missing`).
- `POST` action execution is disabled for MVP:
	- retrain / active-model-switch actions return `not-implemented`
	- other POST actions return `disabled`

Safety constraints:
- No retrain execution.
- No active model mutation.
- No `.joblib` create/overwrite.
- No production/base table write.
- No secret/env value exposure in response.

MVP smoke / regression line:
- Script: `scripts/smoke_model_redesign_workbench.py`
- Output JSON: `reports/model_redesign_workbench_smoke_result.json`
- Coverage:
	- unauth `401`
	- auth missing -> `auth-required/warn`
	- non-premium optional `403`
	- path-input forbidden checks
	- retrain / switch action blocked (`not-implemented` or `disabled`)
	- response secret exposure guard
	- `.active_model.json` unchanged
	- `.joblib` / metadata / reports (except smoke output) unchanged
- Integrated into `scripts/run_keiba_smoke_suite.py` as `model_redesign_workbench` step.

## 16. Implementation Status (P1-4 Read-only Proxy Migration)

Updated: 2026-07-05

Implemented:
- `/api/netkeiba/race-list` Next API route migrated from direct Scrape Service call to FastAPI proxy path.
- FastAPI read-only endpoint added: `GET /api/netkeiba/race-list?date=YYYYMMDD`.
- Existing Next API response shape (`{ raceIds, count }`) preserved.

Not changed:
- `/api/netkeiba/race` write path (Supabase write) remains unchanged in this step.
- No new DB write behavior introduced.

Path after migration:
- UI/Caller -> Next `/api/netkeiba/race-list` -> FastAPI `/api/netkeiba/race-list` -> Scrape Service `/scrape/race_list`.

## 17. Implementation Status (P1-5 Write Path Decomposition + Preflight)

Updated: 2026-07-05

Implemented in this step:
- Added FastAPI read-only endpoint: `GET /api/netkeiba/race/preflight`.
- Preflight performs only:
	- input validation (`race_id` required, 12-digit; optional `date` format check),
	- scrape service reachability/probe,
	- explicit readiness contract return.
- Preflight never performs DB write/Supabase write (`can_write=false`, `write_performed=false`).

Not changed in this step:
- Next API `/api/netkeiba/race` write logic remains unchanged.
- Direct Supabase write responsibility remains in Next route for now.
- Existing UI flow and permission behavior are unchanged.

Preflight response contract:
- `success`: bool
- `status`: `ready | degraded | unavailable`
- `service`: `netkeiba-race`

## 25. Design Freeze (P1.5 Retrain Approval Boundary)

Updated: 2026-07-06

Purpose:
- Freeze approval boundary before implementing actual retrain runtime.
- Clarify what is approved, who approved it, and what becomes executable.

Design-only outputs added:
- `docs/model-retrain-approval-design.md`
- `docs/model-redesign-workbench.md`

Dry-run payload contract (fixed):
- includes core planning fields:
	- `dry_run_id`, `generated_at`, `target`, `model_type`
	- `train_period`, `validation_period`
	- `feature_count`, `selected_features`, `removed_features`
	- `expected_outputs`, `estimated_runtime`, `safety_checks`
- includes traceability fields:
	- `source_model_id`, `active_model_id`
	- `feature_contract_hash`, `data_snapshot_id`
	- `code_version`, `git_commit`, `created_by`, `state`

Approval record contract (fixed):
- `approval_id`, `dry_run_id`
- `approved_by`, `approved_at`, `approval_status`, `approval_comment`
- `approved_payload_hash`
- `requested_by`, `requested_at`, `expires_at`, `invalidation_reason`
- `execution_policy`, `allowed_actions`

Approved retrain preconditions (future execution gate):
- `approval_status=approved`
- payload hash equals `approved_payload_hash`
- not expired (`now <= expires_at`)
- `active_model_id` / `feature_contract_hash` / `code_version` unchanged
- caller role policy satisfied (Admin-first)
- production/base write remains blocked
- model artifact write only with explicit staging/sandbox policy
- active model switch requires separate Admin approval

API design freeze (spec only in this phase):
- active now:
	- `POST /api/model-redesign/summary` (`action=retrain_dry_run`)
- defined for next phase:
	- `POST /api/model-redesign/approval` (`action=create_approval`)
	- `GET /api/model-redesign/approval/:approval_id`
	- `POST /api/model-redesign/job` (`action=submit_approved_retrain`)

UI design freeze (future lanes, runtime-gated):
- dry-run preview
- approval request preview
- approval status
- approved job submit
- job status
- result comparison
- active model switch request

Safety guard continuity:
- no actual retrain implementation in this phase
- no `.joblib` create/overwrite
- no `.active_model.json` mutation
- no active model switch implementation
- no production/base table write enablement
- no path-like input acceptance
- no service_role key usage
- no secret/token/env value exposure in responses/logs

Type-only scaffolding added:
- `src/lib/model-retrain-approval-types.ts`
- shared with the retrain dry-run preview UI/API shapes
- runtime behavior remains unchanged
- `race_id`: string
- `can_scrape`: bool
- `can_write`: false (fixed in this phase)
- `write_performed`: false (fixed in this phase)
- `reason`: string | null

Migration marker:
- `/api/netkeiba/race` is now tracked as `migrationStatus=preflight-added`.
- Next phase is dry-run orchestration without moving production writes yet.

## 18. Implementation Status (P1-6 Preflight Smoke Integration)

Updated: 2026-07-05

Implemented in this step:
- Preflight smoke verdict policy was clarified for operations/CI candidate.
- Added optional strict mode for preflight smoke (`--fail-on-nonready`).
- Added operational smoke suite runner: `scripts/run_keiba_smoke_suite.py`.

Preflight smoke verdict contract:
- `ready` -> pass
- `degraded` -> warn
- `unavailable` -> warn (or skip-equivalent in CI)
- contract error -> fail

Hard fail conditions:
- invalid/non-JSON contract payload
- missing required keys
- `can_write != false`
- `write_performed != false`
- invalid preflight status value

Operational commands:
- Single preflight contract check:
	- `python scripts/smoke_netkeiba_race_preflight.py`
- Strict preflight check:
	- `python scripts/smoke_netkeiba_race_preflight.py --fail-on-nonready`
- Combined smoke suite:
	- `python scripts/run_keiba_smoke_suite.py`

Safety constraints preserved:
- no DB write added
- no Supabase write migration performed
- `/api/netkeiba/race` existing write path unchanged

## 19. Implementation Status (P1-7 Dry-run Orchestration)

Updated: 2026-07-05

Implemented in this step:
- Added FastAPI endpoint: `POST /api/netkeiba/race/dry-run`.
- Dry-run performs:
	- input validation,
	- scrape service reachability/probe,
	- write payload preview generation for `races`, `race_results`, `race_payouts`.

Dry-run fixed safety contract:
- `can_write=false`
- `write_performed=false`
- `dry_run=true`

Not changed in this step:
- Next API `/api/netkeiba/race` existing Supabase write flow remains unchanged.
- No DB/Supabase write is executed by the new FastAPI dry-run endpoint.

Dry-run smoke and suite integration:
- Added script: `scripts/smoke_netkeiba_race_dry_run.py`
- Added output: `reports/netkeiba_race_dry_run_smoke_result.json`
- Integrated into suite: `scripts/run_keiba_smoke_suite.py`

Dry-run smoke verdict:
- `ready` -> pass
- `degraded | unavailable | invalid` -> warn
- contract error -> fail

Operational commands:
- `python scripts/smoke_netkeiba_race_dry_run.py`
- `python scripts/smoke_netkeiba_race_dry_run.py --fail-on-nonready`
- `python scripts/run_keiba_smoke_suite.py`

## 20. Implementation Status (P1-8 Payload Contract Diff)

Updated: 2026-07-05

Implemented in this step:
- Added contract diff script:
	- `scripts/compare_netkeiba_race_payload_contract.py`
- Compared contracts:
	- Next `/api/netkeiba/race` write payload shape (static contract)
	- FastAPI `/api/netkeiba/race/dry-run` preview payload
- Added diff output:
	- `reports/netkeiba_race_payload_contract_diff.json`
- Added smoke suite step:
	- `payload_contract_diff`

Diff categories:
- compatible
- missing_in_dry_run
- extra_in_dry_run
- naming_mismatch
- type_mismatch
- unknown

Verdict policy:
- pass: contracts-compatible
- warn: contract diff detected or dry-run non-ready
- fail: contract error only

Safety constraints preserved:
- no DB write performed
- no Supabase write performed
- Next `/api/netkeiba/race` write path unchanged

Operational commands:
- `python scripts/smoke_netkeiba_race_dry_run.py`
- `python scripts/compare_netkeiba_race_payload_contract.py`
- `python scripts/run_keiba_smoke_suite.py`

## 21. Implementation Status (P1-9 Guarded Write Orchestration)

Updated: 2026-07-05

Implemented in this step:
- Added FastAPI endpoint:
	- `POST /api/netkeiba/race/write`
- Added feature flag guard:
	- `NETKEIBA_RACE_WRITE_ENABLED=false` (default)
- Added strict gate checks:
	- flag enabled
	- `confirm_write=true`
	- `dry_run=false`
	- valid `race_id`
	- dry-run ready + valid preview

Default safety behavior:
- write is rejected when flag is off
- `write_performed=false` guaranteed
- explicit disabled JSON contract returned

Phase boundary:
- actual write execution remains guarded/no-op in this phase
- Next `/api/netkeiba/race` existing write path unchanged
- no DB/Supabase write migration performed

Write guard smoke:
- Added script: `scripts/smoke_netkeiba_race_write_guard.py`
- Added output: `reports/netkeiba_race_write_guard_smoke_result.json`
- Added suite step: `race_write_guard`

Operational commands:
- `python scripts/smoke_netkeiba_race_write_guard.py`
- `python scripts/run_keiba_smoke_suite.py`

## 22. Implementation Status (P1-10 Enabled Guard Verification)

Updated: 2026-07-05

Implemented in this step:
- Extended write guard smoke with enabled-mode matrix:
	- `scripts/smoke_netkeiba_race_write_guard.py --expect-enabled`
- Added enabled-mode report:
	- `reports/netkeiba_race_write_guard_enabled_smoke_result.json`
- Added optional smoke suite step:
	- `scripts/run_keiba_smoke_suite.py --verify-write-guard-enabled`

Validated branches (flag ON process):
- `confirm_write=false` -> `blocked`
- `dry_run=true` -> `blocked`
- invalid `race_id` -> `invalid`
- all preconditions met -> `guarded-noop`

Hard invariant:
- all branches must keep `write_performed=false`
- any `write_performed=true` is fail

Safety constraints preserved:
- no Supabase write executed
- no DB write executed
- Next `/api/netkeiba/race` write path unchanged
- no UI route switch

Operational commands (enabled verification):
- start FastAPI process with temporary env var only
- `python scripts/smoke_netkeiba_race_write_guard.py --expect-enabled`
- `python scripts/run_keiba_smoke_suite.py --verify-write-guard-enabled`

## 23. Implementation Status (P1-11 Staging-only Write Guard Design)

Updated: 2026-07-05

Implemented in this step:
- FastAPI write orchestration lock extended with staging-only double lock:
	- `NETKEIBA_RACE_WRITE_ENABLED=true`
	- `ALLOW_STAGING_WRITE=true`
	- `APP_ENV=staging`
- Request-level hard gates added:
	- `confirm_write=true`
	- `dry_run=false`
	- `payload_contract_approved=true`
- Dry-run preview validator added for writer boundary:
	- target table whitelist check
	- records_count sanity and per-table upper limit check
- Guarded writer stub added (`guarded-stub`):
	- no-op writer interface placeholder
	- explicit TODOs for snapshot/audit/idempotency/rollback

Production safety rule:
- `APP_ENV=production` always returns `blocked`
- flags do not bypass production block

Phase boundary (still no migration of actual write):
- no FastAPI DB write
- no FastAPI Supabase write
- Next `/api/netkeiba/race` write path remains unchanged
- UI route switching is not performed

Smoke updates:
- existing enabled matrix now validates `guarded-stub` path:
	- `python scripts/smoke_netkeiba_race_write_guard.py --expect-enabled`
- optional dedicated checks added:
	- flag-only block: `--expect-flag-only`
	- production hard block: `--expect-production-block`
	- staging lock missing: `--expect-staging-lock-missing`
- suite optional hooks:
	- `--verify-write-guard-flag-only`
	- `--verify-write-guard-production-block`
	- `--verify-write-guard-staging-lock-missing`

Hard invariant:
- any `write_performed=true` is fail

## 24. Implementation Status (P1-12 Staging Writer Stub + Audit/Idempotency)

Updated: 2026-07-05

Implemented in this step:
- staging writer stub contract expanded while keeping no-op behavior:
	- `status=guarded-stub`
	- `write_performed=false`
	- no FastAPI DB/Supabase write
- table whitelist enforced in preview validation:
	- `races`, `race_results`, `race_payouts`
- row count limits enforced:
	- `races <= 1`
	- `race_results <= 30`
	- `race_payouts <= 100`
- idempotency design added (preview only):
	- payload hash generated from guarded request + preview summary
	- idempotency key format: `netkeiba_race:{race_id}:{payload_hash_prefix}`
- audit payload preview added (no persistence):
	- race_id, requested_at, app_env, dry_run, confirm_write
	- target_tables, records_count, payload_hash, write_performed, reason
- snapshot/rollback requirement metadata kept explicit and non-persistent.

Smoke updates:
- enabled mode (`--expect-enabled`) now includes writer-stub contract checks:
	- whitelist/table metadata validation
	- row-limit contract validation
	- idempotency key/payload hash format validation
	- audit payload preview required fields validation
	- row-limit exceeded blocked case validation

Safety constraints preserved:
- Next `/api/netkeiba/race` write path unchanged
- no UI route switch
- no `.env` persistence/commit
- any `write_performed=true` remains fail

## 25. Implementation Status (P1-13 Staging Sandbox-only Write)

Updated: 2026-07-05

Implemented in this step:
- added explicit sandbox write adapter under guarded endpoint.
- actual write is allowed only when all staging + request locks are satisfied and `sandbox_write=true` + `target_mode=sandbox` are provided.
- write destination is restricted to sandbox tables only:
	- `sandbox_netkeiba_races`
	- `sandbox_netkeiba_race_results`
	- `sandbox_netkeiba_race_payouts`
- base tables (`races`, `race_results`, `race_payouts`) are not used for actual write in this phase.
- if sandbox table is missing or schema is not writable, endpoint returns `stopped` with explicit reason and keeps `write_performed=false`.

Additional safety constraints:
- row limits remain enforced (`1/30/100`)
- whitelist remains enforced
- idempotency key is mandatory for sandbox write intent
- production remains always blocked

Response behavior:
- success path: `status=sandbox-written`, `write_performed=true`, includes `target_mode`, `target_tables`, `records_written`, `idempotency_key`, `audit_payload`
- non-success path: `blocked` or `stopped` with `write_performed=false`

Smoke updates:
- new explicit mode:
	- `python scripts/smoke_netkeiba_race_write_guard.py --expect-sandbox-write`
- default smoke/suite remains non-write by default
- suite optional sandbox step:
	- `--verify-write-guard-sandbox-write`

Safety constraints preserved:
- Next `/api/netkeiba/race` write path unchanged
- no UI route switch
- no `.env` commit
- token/secret not exposed

## 26. Implementation Status (P1-14 Sandbox Precheck Read-only)

Updated: 2026-07-05

Implemented in this step:
- added read-only FastAPI endpoint:
	- `GET /api/netkeiba/race/sandbox/precheck`
- precheck verifies only sandbox readiness:
	- expected sandbox tables exist
	- required columns exist
	- required column types are compatible
	- row-limit support metadata is available
	- base table references are not present in sandbox SQL objects
- precheck is non-mutating:
	- no Supabase write
	- no SQLite write
	- no readback mutation path
	- `write_performed=false` fixed

Expected sandbox tables:
- `sandbox_netkeiba_races`
- `sandbox_netkeiba_race_results`
- `sandbox_netkeiba_race_payouts`

Response status contract:
- `ready`: sandbox schema is compatible
- `stopped`: sandbox table missing (or safe stop condition)
- `warn`: schema/type/reference issue detected
- `unavailable`: precheck cannot run safely

Smoke updates:
- new mode:
	- `python scripts/smoke_netkeiba_race_write_guard.py --expect-sandbox-precheck`
- suite optional step:
	- `--verify-write-guard-sandbox-precheck`
- default smoke/suite remain non-write and do not run sandbox precheck unless explicitly requested.

Migration marker:
- `/api/netkeiba/race` tracked as `migrationStatus=sandbox-precheck-added`.

Safety constraints preserved:
- production write remains blocked
- base table write is forbidden in this phase
- Next `/api/netkeiba/race` write path unchanged
- UI flow unchanged

## 27. Implementation Status (P1-14.5 Sandbox DDL / Migration Plan)

Updated: 2026-07-05

Implemented in this step:
- added sandbox-only DDL artifact for manual migration:
	- `docs/migrations/netkeiba_sandbox_tables.sql`
- aligned precheck required columns with migration plan:
	- `race_id`
	- `data|payload`
	- `created_at`
	- `idempotency_key`
	- `payload_hash`
	- `audit_payload`
- rollback/drop sequence documented in SQL comments.

Important constraints:
- no DB auto-apply logic was added,
- no Supabase write was added,
- no production/base table DDL was added,
- no UI flow change,
- no Next write route change.

Ready gate before P1-15:
- precheck must return `status=ready` for all sandbox tables,
- while `stopped`/`warn`, write/readback implementation must not proceed.

## 28. Implementation Status (P1-15 Precheck Ready Verification)

Updated: 2026-07-05

Implemented in this step:
- sandbox DDL was applied manually in local sandbox/staging DB context,
- FastAPI restarted after apply,
- precheck smoke confirmed `ready/pass`:
	- `python scripts/smoke_netkeiba_race_write_guard.py --expect-sandbox-precheck`
	- verdict: `pass` (`sandbox-precheck-ready`)

Explicitly not implemented in this step:
- sandbox write execution,
- write-after-readback verification,
- any production/base table change.

Safety posture:
- phase closed at precheck-ready confirmation,
- write/readback remains deferred to a later phase.

## 29. Implementation Status (P1-16 Sandbox Write + Readback Verification)

Updated: 2026-07-05

Implemented in this step:
- FastAPI `/api/netkeiba/race/write` sandbox path now performs post-write readback verification.
- Readback scope is sandbox tables only:
	- `sandbox_netkeiba_races`
	- `sandbox_netkeiba_race_results`
	- `sandbox_netkeiba_race_payouts`
- Verification key uses `race_id + idempotency_key` and checks:
	- `records_written` vs readback count,
	- sandbox-only target table enforcement,
	- `idempotency_key` match,
	- `payload_hash` match,
	- `audit_payload` presence.

Mismatch contract:
- explicit status `sandbox-readback-mismatch` is returned when readback verification fails.

Operational policy:
- default smoke and default suite remain non-write.
- write + readback is available only by explicit opt-in:
	- `python scripts/smoke_netkeiba_race_write_guard.py --expect-sandbox-write-readback`
	- `python scripts/run_keiba_smoke_suite.py --verify-write-guard-sandbox-write-readback`

Safety constraints preserved:
- production write remains blocked,
- base tables (`races`, `race_results`, `race_payouts`) are not write/readback targets,
- Next `/api/netkeiba/race` route and UI flow remain unchanged.

## 30. UI Frontend Integration Final State

Updated: 2026-07-05

Completed and treated as stable:
- UI page to Next API to FastAPI call chains,
- Premium/Admin UI guard checks,
- dedicated health check contracts,
- route classification and direct-path inventory,
- smoke suite baseline for default UI/API flows,
- lint/build stabilization for frontend and API contract verification.

Not included in UI integration completion:
- P1-16 sandbox write-readback runtime actual pass,
- upstream ready live-write verification,
- sandbox write-readback PR merge decision,
- production/base-table write migration.

Operational rule:
- default UI/API navigation stays non-write,
- sandbox write-readback remains an explicit migration verification step handled separately.

## 31. Implementation Status (P1-7 Fetch Summary History Smoke/E2E)

Updated: 2026-07-07

Implemented:
- Added smoke script: `scripts/smoke_fetch_summary_history.py`
- Added smoke output: `reports/fetch_summary_history_smoke_result.json`
- Integrated into suite: `scripts/run_keiba_smoke_suite.py` as `fetch_summary_history` step
- Added E2E: `e2e/data-collection-history.spec.ts`

Smoke contract:
- unauth request to `/api/scrape/history` is checked (401/403 pass; otherwise warn)
- auth request checks `limit` behavior (`len(jobs) <= limit`)
- response must not expose token/secret/env values (Notion token prefix and env secrets)
- read-only check validates `keiba/data/scrape_jobs.db` row count unchanged before/after
- empty history remains non-fail (`warn` allowed)
- history with `fetch_summary` is normalized and verifies major fields:
	- `mode`
	- `job_id`
	- `updated_at`
	- `estimated_request_count`
	- `cache_hit_count`
	- `cache_miss_count`
	- `resume_hit_count`
	- `elapsed_seconds`
	- `retry_count`

E2E contract:
- Data Collection page shows history section and refresh button
- history cards or empty-state message is rendered
- dry-run/execute display labels remain visible
- secret-like strings are not rendered in UI
- auth-guarded route uses real login pattern and skips when `E2E_PASSWORD` is unset
