# Frontend Data Collection System Design

## 1. 概要

本ドキュメントは、Data Collection フロントエンド（Next.js）とその API プロキシ群、ならびに関連する read-only 計画系 UI（Refresh Plan / P0 Repair Plan）の実装ベース設計を整理したものである。

対象範囲:
- 画面: `src/app/data-collection/page.tsx`, `src/app/data-collection/refresh-plan/page.tsx`, `src/app/data-collection/p0-repair-plan/page.tsx`
- Next API route: `src/app/api/scrape/*`, `src/app/api/data-stats/route.ts`, `src/app/api/races/*`
- FastAPI scrape API: `python-api/routers/scrape.py`
- 計画/監査スクリプト: `scripts/plan_scrape_refresh.py`, `scripts/plan_p0_scrape_repair.py`, `scripts/plan_p0_targeted_refetch.py`, `scripts/validate_p0_targeted_refetch_live.py`, `scripts/diagnose_source_empty_result_cells.py`

設計方針:
- 本実行系（scrape start）は Next route から FastAPI に委譲。
- 監査/計画系は read-only を原則にし、UI で Execute を無効化。
- dry-run は「未完了」と「0件」を混同しない表示設計（pending/error/complete 分離）。
- 認可は `authFetch` + `Authorization: Bearer` を透過し、必要 route で role/tier 判定。

---

## 2. 画面一覧

1) Data Collection
- パス: `/data-collection`
- 目的: 期間指定 dry-run / 実取得、履歴表示、取得済みデータ確認、API 健康状態確認、プロファイリング起動。

2) Refresh Plan (Dry-run)
- パス: `/data-collection/refresh-plan`
- 目的: refresh 方針のプランのみ生成（DB 更新なし、スクレイプ実行なし）。

3) P0 Repair Plan (Read-only)
- パス: `/data-collection/p0-repair-plan`
- 目的: P0 欠損/整合性異常の修復計画をプレビュー（実修復なし）。

---

## 3. Data Collection 画面フロー

主要 state:
- 期間: `startPeriod`, `endPeriod`
- 実行オプション: `forceRescrape`
- dry-run: `dryRunLoading`, `dryRunStartedAt`, `dryRunElapsedSeconds`, `dryRunError`, `dryRunResultReady`, `dryRunResult`, `dryRunExecuted`
- 実行: `useBatchScrape()` 由来 `batchLoading`, `batchProgress`, `batchResult`
- 履歴: `fetchHistory`, `fetchHistoryLoading`
- 統計/データ一覧: `dataStats`, `showCollectedData`, `collectedRaces`, `selectedRaceDetail`
- API 健康: `localApiStatus`, `localApiReason`

初期化:
- `useEffect` で `loadStats()`, `checkLocalApi()`, `loadFetchSummaryHistory()`。

ユーザー操作:
1. 期間選択（年月）
2. dry-run 実行または本実行
3. 履歴・統計・レース詳細を確認

UI ガード:
- API が `unhealthy/unknown` の場合は実行ボタンを disable。
- dry-run 実行中は結果カードを表示せず、進行中カードのみ表示。

---

## 4. Dry-run フロー

入口:
- UI: `/data-collection` の `handleDryRun()`
- Next route: `POST /api/scrape`
- FastAPI: `POST /api/scrape/start` (job 作成)

処理:
1. UI が `startPeriod/endPeriod` を `YYYYMMDD` に変換。
2. `POST /api/scrape` に `dry_run: true` で起動要求。
3. 返却 `job_id` を使い `GET /api/scrape/status/{job_id}` を 1 秒間隔ポーリング。
4. `completed` で `result.fetch_summary.dry_run` を正規化して表示。
5. 最大 90 回（約 90 秒）で未完了の場合は timeout エラー表示。

表示設計:
- 実行中: 「見積もり生成中」「経過秒」を表示。
- 期間が 6 ヶ月以上の場合: 長時間化注意を表示。
- 未完了時: 0 値を表示せず、明示エラーに遷移。

dry-run 表示項目:
- `total_target_count`, `unique_url_count`, `estimated_request_count`
- `cache_hit_count`, `cache_miss_count`, `resume_hit_count`, `skipped_count`
- `db_existing_skip_count`, `db_existing_race_count`, `db_existing_horse_count`, `db_existing_result_count`, `db_existing_pedigree_count`
- `new_fetch_required_count`, `already_covered_count`
- `estimated_runtime_sec`
- rate-limit/retry-backoff/circuit-breaker policy

指標補足:
- `skipped_count`: 既存互換（cache hit + resume hit）
- `db_existing_skip_count`: DB保存済み判定によるスキップ件数
- `new_fetch_required_count`: 新規にフェッチが必要な件数
- `already_covered_count`: 再利用可能件数（cache/resume/DB existing の合計）

---

## 5. 実取得フロー

入口:
- UI: `/data-collection` の `handlePeriodBatchScrape()`
- 内部フック: `useBatchScrape()`

処理:
1. 期間妥当性チェック（start <= end）。
2. 月数算出し、確認ダイアログ表示。
3. dry-run 未実行の場合は警告文を表示するが実行は許可。
4. `useBatchScrape()` が月単位でバックエンド処理を進行。
5. 完了後、統計と履歴を再読み込み。

出力:
- 完了サマリ（期間、月数、レース数、経過秒）。
- 進捗バー（`batchProgress.current/message/eta`）。

---

## 6. fetch summary 履歴

データ取得:
- UI: `loadFetchSummaryHistory()`
- Next route: `GET /api/scrape/history?limit=10`
- FastAPI: `GET /api/scrape/history`

表示:
- `mode === dry-run` は見積系指標を表示。
- それ以外（execute）は保存件数・ネットワーク件数・リトライ等を表示。
- `fetch_summary` が存在する job のみ表示。

運用上の意義:
- dry-run と execute の監査ログを同一 UI で比較可能。
- 長期期間における見積差分と実績差分を追跡可能。

---

## 7. 取得済みデータ表示

統計:
- `GET /api/data-stats?ultimate=true`
- 表示: 総レース数、総出走馬数、最終取得日

レース一覧:
- `GET /api/races/recent?limit=50`
- 折りたたみ表示 + 更新ボタン

レース詳細:
- `GET /api/races/{race_id}/horses`
- モーダルで結果テーブル表示

---

## 8. Refresh Plan UI

画面:
- `/data-collection/refresh-plan`

入力:
- `startDate`, `endDate`, `target`, `policy`, `staleDays`, `currentParserVersion`

操作:
- `Generate Dry-run Plan` で `POST /api/scrape/refresh-plan`
- `Execute Refresh` は disabled（UI レベル）

表示:
- summary counters
- warnings/verdict
- action group 別 decisions サンプル

制約:
- 「dry-run preview のみ」を明示。
- 実スクレイプ・DB 更新はこの画面では発火しない。

---

## 9. P0 Repair Plan UI

画面:
- `/data-collection/p0-repair-plan`

入力:
- `target`

操作:
- `Generate P0 Repair Plan` で `POST /api/scrape/p0-repair-plan`
- `Execute P0 Repair` / `Execute Refetch` は disabled（UI レベル）

表示:
- P0 総数、action/reason breakdown、sample targets、recommended actions

制約:
- read-only preview のみ。

---

## 10. P0 Targeted Refetch / Live Validation の位置づけ

現状:
- UI 画面としては未実装（Data Collection から直接操作する経路なし）。
- スクリプト運用ベースで実行。

関連スクリプト:
- `scripts/plan_p0_targeted_refetch.py`
  - read-only dry-run plan を作成（HTTP 実行なし、DB write なし）
- `scripts/validate_p0_targeted_refetch_live.py`
  - 小規模 live validation（上限件数）を実施
  - upsert/repair 実行は行わない
- `scripts/diagnose_source_empty_result_cells.py`
  - live validation 出力 + cache を読んで原因分類
  - `cache-missing` と `alternate-page-required` を分離

今後の UI 化候補:
- refetch plan preview
- live validation 実行トリガ
- source-empty 診断結果ビュー

---

## 11. API route 契約（Frontend -> Next -> FastAPI/Script）

### 11.1 Data Collection 主要 API

| frontend screen | Next route | FastAPI/Python script | method | input | output | read-only | external HTTP | DB write | UI fields |
|---|---|---|---|---|---|---|---|---|---|
| Data Collection | `/api/scrape` | FastAPI `/api/scrape/start` | POST | `start_date,end_date,force_rescrape,dry_run` | `job_id` | dry_run時は実質 read-only、execute時は no | dry_run: no / execute: yes | dry_run: no / execute: yes | dry-run開始/実行開始 |
| Data Collection | `/api/scrape/status/{jobId}` | FastAPI `/api/scrape/status/{job_id}` | GET | path `jobId` | `status, progress, result/error` | yes | no | no | 進捗、dry-run結果、完了判定 |
| Data Collection | `/api/scrape/history` | FastAPI `/api/scrape/history` | GET | `limit` | jobs 配列 | yes | no | no | fetch summary 履歴 |
| Data Collection | `/api/scrape/health` | FastAPI `/api/scrape/health` | GET | なし | `status, reason, metrics` | yes | no | no | API 稼働表示 |
| Data Collection | `/api/data-stats` | FastAPI `/api/data_stats` | GET | query passthrough | 統計 JSON | yes | no | no | 総レース数等 |
| Data Collection | `/api/races/recent` | FastAPI `/api/races/recent` | GET | `limit` | races 配列 | yes | no | no | 最近レース一覧 |
| Data Collection | `/api/races/{race_id}/horses` | FastAPI `/api/races/{race_id}/horses` | GET | path `race_id` | horses 配列 | yes | no | no | レース詳細モーダル |

### 11.2 Refresh/P0 Plan API

| frontend screen | Next route | FastAPI/Python script | method | input | output | read-only | external HTTP | DB write | UI fields |
|---|---|---|---|---|---|---|---|---|---|
| Refresh Plan | `/api/scrape/refresh-plan` | `scripts/plan_scrape_refresh.py` | POST/GET | `startDate,endDate,target,policy,staleDays,currentParserVersion` | `dry_run,update_enabled=false,plan{...}` | yes | no | no | summary, warnings, decisions |
| Refresh Plan | `/api/scrape/refresh-plan` | (実行系なし) | PUT | なし | 501 `not-implemented` | yes | no | no | Execute disabled |
| P0 Repair Plan | `/api/scrape/p0-repair-plan` | `scripts/plan_p0_scrape_repair.py` | POST/GET | `target` | `dry_run,read_only,update_enabled=false,plan{...}` | yes | no | no | summary, breakdown, samples |
| P0 Repair Plan | `/api/scrape/p0-repair-plan` | (実行系なし) | PUT | なし | 501 `not-implemented` | yes | no | no | Execute disabled |

### 11.3 補助 API（既存）

| frontend screen | Next route | FastAPI/Python script | method | input | output | read-only | external HTTP | DB write | UI fields |
|---|---|---|---|---|---|---|---|---|---|
| （運用/API用） | `/api/scrape/repair/{race_id}` | FastAPI `/api/scrape/repair/{race_id}` | POST | path `race_id` | repair response | no | あり得る | あり得る | 現在UI未接続 |
| （運用/API用） | `/api/scrape/rescrape-incomplete` | FastAPI `/api/rescrape_incomplete` | POST | `limit` (query) | rescrape response | no | yes | yes | 現在UI未接続 |

---

## 12. 状態管理

Data Collection:
- ローカル `useState` で画面状態を保持。
- 長時間ジョブは `useBatchScrape`（実行）と `useJobPoller`（汎用ポーリング）で抽象化。

Refresh/P0:
- 単画面内 state 管理（`loading/error/plan` + フォーム state）。

認証:
- `authFetch` が Supabase session の access_token を取り、Authorization ヘッダ付与。
- route 側はヘッダを FastAPI または Supabase authz に透過。

エラーハンドリング:
- route では `AbortSignal.timeout(...)` を設定。
- UI は `res.ok` で分岐し、非200時は body `error/detail` を優先表示。

---

## 13. 安全ガード

実装済みガード:
1. Refresh/P0 route で Supabase role/tier 認可（admin or premium）。
2. `FORBIDDEN_PATH_KEYS` による危険入力キー拒否（path/output/dbPath 等）。
3. `PUT` は 501 を返し、実行系を明示的に無効化。
4. レスポンスに `update_enabled=false`, `update_action='not-implemented'` を付与。
5. planner 子プロセスは timeout（120秒）付きで実行。
6. `sanitizeError` で secret/token らしき文字列をマスク。
7. UI 側ボタンを disabled にして誤操作防止。

不変条件との整合:
- dry-run は HTTP 実アクセスを行わないプレビューを前提。
- 実行系タイムアウト・ポーリングは長期レンジでも未完了を 0 と誤解しない設計。

---

## 14. 現在の課題

1. Data Collection の実行フローは `useBatchScrape` 依存が大きく、統一状態機械（state machine）化されていない。
2. Refresh/P0 は read-only 完成度が高いが、execution phase が未実装。
3. targeted refetch / live validation / source-empty diagnosis が UI 非統合で運用手順が分断。
4. API 契約の型共有（TS type <-> backend schema）が限定的で、手動同期コストが残る。
5. 長時間処理での UX（ポーリング頻度、中断/再開、ジョブ再接続）に改善余地。

---

## 15. 改善ロードマップ

Phase 1 (短期):
- Data Collection の dry-run/execute 状態を共通 state machine 化。
- fetch summary 履歴にフィルタ（mode/status/date）を追加。
- エラー分類（timeout/auth/network/backend）を UI で明示。

Phase 2 (中期):
- targeted refetch plan / live validation / source-empty diagnosis の専用 UI を追加。
- Refresh/P0 の API 契約を OpenAPI or zod schema で固定。
- 実行前チェックリスト（read-only / risk / expected http count）をガード UI として導入。

Phase 3 (長期):
- Refresh/P0 execution phase を段階解放（staging guard -> limited rollout）。
- ジョブキュー可視化（cancel/retry/resume）を導入。
- 監査レポートを画面内で一元化し、運用スクリプト依存を縮小。

---

## 16. 関連ファイル一覧

### Frontend pages
- `src/app/data-collection/page.tsx`
- `src/app/data-collection/refresh-plan/page.tsx`
- `src/app/data-collection/p0-repair-plan/page.tsx`

### Next API routes
- `src/app/api/scrape/route.ts`
- `src/app/api/scrape/status/[jobId]/route.ts`
- `src/app/api/scrape/history/route.ts`
- `src/app/api/scrape/health/route.ts`
- `src/app/api/scrape/refresh-plan/route.ts`
- `src/app/api/scrape/p0-repair-plan/route.ts`
- `src/app/api/scrape/repair/[race_id]/route.ts`
- `src/app/api/scrape/rescrape-incomplete/route.ts`
- `src/app/api/data-stats/route.ts`
- `src/app/api/races/recent/route.ts`
- `src/app/api/races/[race_id]/horses/route.ts`

### Frontend hooks/libs
- `src/hooks/useBatchScrape.ts`
- `src/hooks/useJobPoller.ts`
- `src/lib/auth-fetch.ts`

### Backend/FastAPI
- `python-api/routers/scrape.py`

### Planning/Audit scripts
- `scripts/plan_scrape_refresh.py`
- `scripts/plan_p0_scrape_repair.py`
- `scripts/plan_p0_targeted_refetch.py`
- `scripts/validate_p0_targeted_refetch_live.py`
- `scripts/diagnose_source_empty_result_cells.py`

---

## 補足: この文書の前提

- 実装ベース（as-is）記述であり、未実装機能は「未実装」として明示。
- read-only フローの定義は、UI 表示・route 契約・スクリプト docstring の三層で確認済み。
