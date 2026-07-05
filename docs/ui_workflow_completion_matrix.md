# UI Workflow Completion Matrix (keiba-ai-pro)

Updated: 2026-07-05
Scope: UI (src/app) + Next API (src/app/api) + FastAPI router/script mapping inventory

## 0. 前提と判定ルール

- 連携基盤 (UI -> Next API -> FastAPI) は完成前提。
- 本ドキュメントは「業務がUIで完結するか」を判定する。
- CLI/Notebookでのみ実行できる機能は UI 完成扱いにしない。
- 本番 safety 制約:
  - production/base table write は禁止
  - NETKEIBA_RACE_WRITE_ENABLED=true を前提にしない
  - ALLOW_STAGING_WRITE=true を前提にしない
  - sandbox write-readback を通常UIに混在させない

分類定義:

- complete_ui: UIから入力 -> 実行 -> 結果確認まで完結
- partial_ui: UIはあるが一部がCLI/Script依存、または成果物確認がUI外
- api_only: APIはあるがUI呼び出しがない
- script_only: Script/Notebookのみ可能
- blocked: safety上または現フェーズ方針で本番不可
- unknown: 実装確認不足

---

## 1. UI画面一覧 (実装存在)

| 画面 | 主目的 | 主入力/操作 | 主な Next API |
|---|---|---|---|
| /home | ハブ/状態確認 | 4-step導線, API状態確認 | /api/health, /api/data-stats |
| /data-collection | データ取得/プロファイリング | 期間(月), 強制再取得, 取得開始, API health, profiling開始 | /api/scrape, /api/scrape/status/[jobId], /api/scrape/health, /api/profiling, /api/profiling/status/[job_id], /api/races/recent, /api/races/[race_id]/horses |
| /data-view (Premium) | データ検証/特徴量確認 | 日付, レース選択, raw/featuresタブ, 列フィルタ | /api/races/by-date, /api/debug/race/[race_id], /api/debug/race/[race_id]/features |
| /feature-lab (Premium) | 特徴量分析 | target, importance_type, topN, summary/importance/coverageタブ | /api/features/summary, /api/features/importance, /api/features/coverage |
| /train | モデル学習/モデル管理 | target, model_type, 学習期間, advanced設定, Optuna, 学習開始, activate/delete | /api/ml/train/start, /api/ml/train/status/[job_id], /api/models, /api/models/[id], /api/models/[id]/activate |
| /predict-batch | 一括予測/購入/エクスポート | 日付, venue filter, model選択, 予測実行, odds refresh, 購入記録, JSON/CSV export | /api/races/by-date, /api/analyze-race, /api/realtime-odds/refresh, /api/realtime-odds/[race_id], /api/purchase, /api/export/bet-list |
| /race-analysis | 単レース予測詳細/結果照合 | 日付, レース選択, モデル選択, predict/features/resultタブ | /api/races/by-date, /api/analyze-race, /api/models, /api/debug/race/[race_id]/features, /api/prediction-history/[race_id], /api/races/[race_id]/horses |
| /prediction-history (Premium) | 予測分析/成績追跡 | 更新, レース一覧, race-analysisへの遷移 | /api/prediction-history |
| /production-readiness (Premium/Admin) | 本番前 read-only チェック | 実行ボタン, pass/warn/fail表示, 結果要約JSON | /api/production-readiness |
| /dashboard | 購入履歴/損益分析 | 結果入力(hit/miss,payout), delete, ソート | /api/purchase-history, /api/purchase/[id], /api/statistics, /api/data-stats |
| /admin (AdminOnly) | 管理運用 | user role変更, stats確認 | Supabase profiles直接 + /api/data-stats |
| /login | 認証 | login/signup タブ, email/password submit | Supabase Auth SDK 直接 |
| / | ランディング | 遷移のみ | (直接API呼び出しなし) |

---

## 2. UI操作 -> Next API -> FastAPI/script マッピング

| 業務操作 | UI | Next API | FastAPI endpoint / script | 備考 |
|---|---|---|---|---|
| 期間スクレイプ開始 | /data-collection | POST /api/scrape | POST /api/scrape/start (FastAPI scrape router) | 月単位ループ + job polling |
| スクレイプ進捗監視 | /data-collection, /predict-batch hooks | GET /api/scrape/status/[jobId] | GET /api/scrape/status/{job_id} | useJobPoller |
| スクレイプhealth | /data-collection | GET /api/scrape/health | GET /api/scrape/health | read-only health |
| 取得済み一覧/詳細 | /data-collection | GET /api/races/recent, GET /api/races/[race_id]/horses | GET /api/races/recent, GET /api/races/{race_id}/horses | 結果表示あり |
| Profiling起動 | /data-collection | POST /api/profiling | POST /api/profiling/start | レポート閲覧UIは限定 |
| Profiling進捗 | /data-collection | GET /api/profiling/status/[job_id] | GET /api/profiling/status/{job_id} | job statusのみ |
| 学習開始 | /train | POST /api/ml/train/start | POST /api/train/start | async job |
| 学習進捗 | /train | GET /api/ml/train/status/[job_id] | GET /api/train/status/{job_id} | progress表示あり |
| モデル一覧/切替/削除 | /train | /api/models, /api/models/[id], /api/models/[id]/activate | /api/models, /api/models/{id}, /api/models/{id}/activate | UI完結 |
| 一括予測 | /predict-batch | POST /api/analyze-race | POST /api/analyze_race | CONCURRENCY=1 |
| 単レース予測 | /race-analysis | POST /api/analyze-race | POST /api/analyze_race | cache + fallback表示 |
| 予測結果照合 | /race-analysis | GET /api/prediction-history/[race_id] | GET /api/prediction-history/{race_id} | Premium |
| 予測履歴分析 | /prediction-history | GET /api/prediction-history | GET /api/prediction-history | Premium |
| 特徴量サマリ/重要度/coverage | /feature-lab | /api/features/summary, /importance, /coverage | 同名 FastAPI endpoints | Premium |
| レースraw/features検証 | /data-view | /api/debug/race/[race_id], /features | GET /api/debug/race/{race_id}, /features | Premium |
| 購入記録 | /predict-batch | POST /api/purchase | POST /api/purchase | 結果入力はdashboard |
| 購入結果更新/削除 | /dashboard | PATCH/DELETE /api/purchase/[id] | PATCH/DELETE /api/purchase/{id} | UI完結 |
| 損益統計 | /dashboard | GET /api/statistics | GET /api/statistics | UIグラフ表示 |
| 本番前チェック実行 | /production-readiness | POST /api/production-readiness | health fetch + allowlist command 実行 (read-only) | write API は呼ばない |

---

## 3. 業務フロー完成度マトリクス (13フロー)

| # | 業務フロー | UIあり | 実行可能 | 結果表示 | 分類 | complete/partial/missing | 本番利用 | 根拠メモ |
|---|---|---|---|---|---|---|---|---|
| 1 | データ取得 | yes | yes | yes | complete_ui | complete | OK (read/scrape運用) | /data-collection で期間指定+進捗+件数表示 |
| 2 | データ検証 | yes | yes | yes | complete_ui | complete | OK | /data-view, /data-collection recent/details |
| 3 | 特徴量生成 | yes | yes (予測/学習時に内部生成) | partial | partial_ui | partial | OK | 生成自体はbackend内部。専用「生成実行画面」はなし |
| 4 | 特徴量分析 | yes | yes | yes | complete_ui | complete | OK (Premium) | /feature-lab summary/importance/coverage |
| 5 | モデル学習 | yes | yes | yes | complete_ui | complete | 条件付きOK (権限制御前提) | /train start/status/result/models |
| 6 | モデル評価 | yes | partial | partial | partial_ui | partial | 条件付きOK | AUC/logloss/履歴ROIは表示。高度評価(反復比較)はUI外 |
| 7 | 予測 | yes | yes | yes | complete_ui | complete | OK | /predict-batch, /race-analysis |
| 8 | 予測結果の分析 | yes | yes | yes | complete_ui | complete | OK (Premium含む) | /prediction-history + /race-analysis result tab + /dashboard |
| 9 | モデル再設計・改善提案 | no (専用画面なし) | no (UI) | no (UI) | script_only | missing | NG | optimizer.py / notebooks 依存 |
| 10 | Notionレポート出力 | yes | yes (Premium/Admin) | yes | partial_ui | partial | 条件付きOK | /notion-report + /api/notion-report で preview -> send。token未設定時は config-missing/warn |
| 11 | 本番運用前チェック | yes | yes (read-only scope) | yes | partial_ui | partial | 条件付きOK | /production-readiness で health/smoke/flag/secret/git を集約 |
| 12 | smoke / health check | partial | partial | partial | partial_ui | partial | 条件付きOK | /api/health, /api/scrape/health はUI可視。smoke suiteはscript |
| 13 | 権限ガード | yes | yes | yes | partial_ui | partial | OK | AuthContext, AdminOnly, Premium制御はあるが一部backend依存 |

要約判定:

- complete: 1,2,4,5,7,8
- partial: 3,6,10,11,12,13
- missing: 9

---

## 4. APIはあるがUIがない機能一覧 (api_only)

Next API routeは存在するが、主要業務UI導線で未使用/非表示の機能:

- /api/ai-correct
- /api/ocr
- /api/netkeiba/calendar
- /api/netkeiba/race-list
- /api/netkeiba/race
- /api/backfill/nar-pedigree
- /api/backfill/coat-color
- /api/scrape/repair/[race_id]
- /api/scrape/rescrape-incomplete
- /api/features/catalog
- /api/export/data
- /api/export/db
- /api/data/all (destructive utility)
- /api/debug/race-ids
- /api/analyze-races-batch (UIは /api/analyze-race を逐次呼び出し)

注記:

- /api/stripe/* は課金系内部導線であり、本業務13フローの対象外。

---

## 5. Script/Notebookでしかできない機能一覧 (script_only)

- 反復最適化と改善提案生成:
  - python-api/training/optimizer.py
  - 出力: docs/reports/iter_*_metrics.json (recommendations)
- Notebook E2E監査:
  - scripts/run_keiba_notebook_e2e.py
- 本番前 smoke suite:
  - scripts/run_keiba_smoke_suite.py
  - scripts/smoke_*.py
- compile/lint/build 一括品質ゲート運用 (CLI)
- Notion向け出力処理:
   - `/notion-report` + `/api/notion-report` で UI 導線あり
   - reportType は allowlist 固定（任意ファイルパス指定なし）
   - token は server-side env のみ（UI/レスポンスで実値非表示）

---

## 6. 本番利用OKの機能一覧

- /predict-batch 一括予測 + 購入記録
- /race-analysis 単レース予測 + (Premium)特徴量/結果照合
- /feature-lab 特徴量分析 (Premium)
- /data-view データ検証 (Premium)
- /data-collection の read/scrape start/status/health/profiling start
- /dashboard 購入履歴更新と損益分析
- /home の health/data stats 可視化
- /admin (AdminOnly) のユーザー管理

条件:

- backend権限ガードを維持
- write系の実データ書込ガード (production/staging lock) を無効化しない

---

## 7. 本番利用NGまたは通常UIから禁止すべき操作

- /api/netkeiba/race/write の本番書込
  - production は明示的 blocked
  - staging でも strict guard + sandbox条件必須
- /api/data/all (destructive admin utility)
- sandbox write-readback 系運用を通常ユーザーUIに露出すること
- 反復最適化/再設計を本番UIボタン化して即時実行すること (まずガード付き運用画面が必要)

---

## 8. UIに存在しないが必要な画面一覧

優先度高:

1. モデル再設計ワークベンチ
   - 目的: 改善提案生成 -> 反映候補比較 -> 再学習起動 -> 評価比較
2. Notionレポート出力画面
   - 目的: 学習/評価/予測分析結果をテンプレ化してexport
優先度中:

3. API-only運用機能のAdmin画面
   - scrape repair/rescrape-incomplete/backfill/debug-race-ids
4. Profiling結果ビュー画面
   - /api/profiling/html/[job_id] をUIで参照

---

## 9. 次に実装すべきUI機能の優先順位

1. P1: モデル再設計・改善提案 UI
   - optimizer結果の可視化
   - 採用/却下の意思決定フロー
   - 再学習ジョブ連携
2. P2: Notionレポート出力 UI
   - 出力対象/期間/テンプレ選択
   - secretはserver-side envのみ
3. P2: Profiling report viewer
4. P3: API-only admin utilities の安全な集約 (role=admin + explicit confirm)

---

## 10. docs/frontend_ui_backend_contract.md への追記案

提案セクション名:

- "16. Workflow Completion Contract (UI Business Flows)"

追記案本文:

1. 業務フロー13項目に対して、各リリースで complete_ui / partial_ui / script_only を更新する。
2. complete_ui の定義を固定する:
   - 入力UIあり
   - 実行トリガーあり
   - loading表示あり
   - 成功/失敗表示あり
   - 権限不足時のUXがある
3. script_only から complete_ui へ昇格する際は、以下を必須化:
   - role guard (admin/premium)
   - production write guard
   - dry-run / preview / confirm
   - audit log
4. Notion/外部連携は token をfrontendに露出しない。
5. 本番運用前チェックは read-only dashboard を原則とし、破壊操作を混在させない。

---

## 11. 現時点の正確な表現

- UIとAPIの接続基盤は完成。
- 予測/分析系UIは本番運用確認フェーズにある。
- ただし、データ取得 -> 学習 -> 再設計 -> Notion出力までの全業務が UI 完結とはまだ断定できない。
- 現在は、CLI/Notebook依存の業務 (再設計, Notion出力) が残っている。

## 13. P0実装更新 (2026-07-05)

- 本番前チェック画面 `/production-readiness` を追加。
- Next API `/api/production-readiness` を追加。
- チェック対象:
   - Frontend build
   - FastAPI health
   - scrape health
   - analyze_race smoke
   - smoke suite summary
   - secret scan (Notion token prefix)
   - git status 注意
   - write flag (`NETKEIBA_RACE_WRITE_ENABLED=false`, `ALLOW_STAGING_WRITE=false`)
   - APP_ENV safety
   - sandbox write-readback 別管理確認
   - production/base table write 禁止確認
- 実行は allowlist command のみ。write系 endpoint は呼ばない。

## 12. 検証実行メモ (今回コミット範囲外)

- 認証トークン未設定時の smoke 401/403 は `auth-required` として warn 扱い。
- `KEIBA_AUTH_BEARER_TOKEN` 設定時は認証必須 smoke を通常の pass/fail で評価する。
- 失敗詳細は reports 側の生成物に記録されるが、本コミットには含めない。
