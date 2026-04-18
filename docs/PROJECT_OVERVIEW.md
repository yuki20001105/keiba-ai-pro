# KEIBA-AI-PRO プロジェクト�E体構�E

> 最終更新: 2026-04-12

---

## シスチE��全体像

```
ブラウザ�E�ユーザー�E�E
    ━E
    ▼
┌─────────────────────────────────────━E
━E Next.js  (src/)  :3000             ━E ↁEフロントエンチE+ API プロキシ層
━E TypeScript / React / Tailwind CSS  ━E
└────────────┬────────────────────────━E
             ━EHTTP (localhost:8000)
             ▼
┌─────────────────────────────────────━E
━E FastAPI  (python-api/)  :8000      ━E ↁEML バックエンチE
━E routers/ + scraping/ + models/     ━E
└────────────┬────────────────────────━E
             ━EPython import
             ▼
┌─────────────────────────────────────━E
━E keiba_ai  (keiba/keiba_ai/)        ━E ↁEコア ML ライブラリ
━E スクレイピング / 特徴釁E/ 学翁E     ━E
└────────────┬────────────────────────━E
             ━ESQLite3
             ▼
┌─────────────────────────────────────━E
━E keiba_ultimate.db (keiba/data/)    ━E ↁEローカル本番 DB
━E 14,000+ レース / 180,000+ 馬チE�Eタ ━E
└─────────────────────────────────────━E
```

---

## 技術スタチE��早見表

| レイヤー | 技衁E| 用送E|
|---|---|---|
| フロントエンチE| Next.js 14 / TypeScript / Tailwind CSS | WebUI・API Routes |
| ML バックエンチE| FastAPI / Python 3.11 | 学習�E予測 API |
| ML 本佁E| LightGBM / scikit-learn / Optuna | モチE��学習�E最適匁E|
| スクレイピング | requests / Playwright | チE�Eタ収集 |
| ローカル DB | SQLite�E�EAL モード！E| レース・予測チE�Eタ |
| クラウチEDB | Supabase�E�EostgreSQL�E�E| ユーザー認証・課金管琁E|
| 課釁E| Stripe | 有料プラン |
| AI 補助 | OpenAI GPT / Google Gemini | OCR 補正 |
| OCR | Google Vision API | 馬券画像読み取り |
| PWA | Service Worker | モバイルアプリ匁E|
| チE�Eロイ | Railway�E�EastAPI�E�E Vercel�E�Eext.js�E�E| 本番環墁E|

---

## チE��レクトリ構�Eと役割

---

### `/src/`  ENext.js フロントエンチE

#### `/src/app/`  Eペ�Eジ�E�Epp Router�E�E

| パス | 役割 |
|---|---|
| `page.tsx` | トップ（ルートリダイレクト！E|
| `layout.tsx` | 全ペ�Eジ共通レイアウト（�EチE��ー・PWA 設定！E|
| `home/` | ホ�Eム画面 |
| `dashboard/` | ダチE��ュボ�Eド（予測結果・統計一覧�E�E|
| `predict-batch/` | 一括予測�E�レース選抁EↁEまとめて予測�E�E|
| `train/` | モチE��学習（学習開始�E進捗確認！E|
| `data-collection/` | チE�Eタ収集操佁EUI�E�スクレイピング期間持E��！E|
| `data-view/` | 取得済みチE�Eタの閲覧 |
| `race-analysis/` | 単一レースの詳細刁E��ビュー |
| `admin/` | 管琁E��E��用�E�ユーザー管琁E�EシスチE��設定！E|

#### `/src/app/api/`  ENext.js API Routes�E��Eロキシ層 + 軽量�E琁E��E

**スクレイピング系**

| ルーチE| 役割 |
|---|---|
| `scrape` | スクレイピングジョブ開始！EastAPI へプロキシ�E�E|
| `scrape/status/[jobId]` | ジョブ進捗取征E|
| `scrape/repair/[race_id]` | 個別レースチE�Eタ修復 |
| `scrape/rescrape-incomplete` | 不完�EチE�Eタの再スクレイチE|
| `netkeiba/race-list` | 持E��日のレース一覧取征E|
| `netkeiba/race` | 個別レース詳細取征E|
| `netkeiba/calendar` | レースカレンダー取征E|

**レース・チE�Eタ系**

| ルーチE| 役割 |
|---|---|
| `races/by-date` | 持E��日のレース一覧�E�EB から�E�E|
| `races/recent` | 最近取得したレース一覧�E�軽量！E|
| `races/[race_id]/horses` | 出走馬一覧�E�EL 推論なし！E|
| `data-stats` | DB 統計（レース数・馬数・最終取得日�E�E|
| `data/all` | 全チE�Eタ削除�E�開発用�E�E|
| `backfill/coat-color` | 馬の毛色チE�Eタ補宁E|
| `backfill/nar-pedigree` | NAR 血統チE�Eタ補宁E|

**ML 系**

| ルーチE| 役割 |
|---|---|
| `ml/train/start` | モチE��学習開始！EastAPI へプロキシ�E�E|
| `ml/train/status/[job_id]` | 学習進捗取征E|
| `models` | 保存済みモチE��一覧 |
| `models/[id]` | 特定モチE��の詳細・削除 |
| `analyze-race` | 単一レース刁E��・予測�E�EastAPI へプロキシ�E�E|
| `analyze-races-batch` | 褁E��レース一括刁E�� |
| `profiling` | チE�Eタプロファイリング開姁E|
| `profiling/status/[job_id]` | プロファイリング進捁E|
| `profiling/html/[job_id]` | プロファイルレポ�Eト取征E|

**購入・収支系**

| ルーチE| 役割 |
|---|---|
| `purchase` | 購入推奨の保孁E|
| `purchase/[id]` | 特定購入の取得�E更新 |
| `purchase-history` | 購入履歴一覧 |
| `statistics` | 皁E��玁E�E収支統訁E|
| `realtime-odds/[race_id]` | リアルタイムオチE��取征E|
| `realtime-odds/refresh` | オチE��強制更新 |
| `export/bet-list` | 馬券リスチECSV エクスポ�EチE|
| `export/data` | チE�Eタ CSV エクスポ�EチE|
| `export/db` | DB エクスポ�EチE|

**外部サービス・そ�E仁E*

| ルーチE| 役割 |
|---|---|
| `ocr` | Google Vision API で馬券画像を OCR 読み取り |
| `ai-correct` | OCR 結果めEAI�E�EpenAI/Gemini�E�で補正 |
| `stripe/create-checkout` | Stripe 決済セチE��ョン作�E |
| `stripe/portal` | Stripe カスタマ�Eポ�Eタル |
| `stripe/webhook` | Stripe Webhook 受信�E�支払い完亁E��知�E�E|
| `health` | ヘルスチェチE�� |
| `debug/race/[race_id]` | 生データ確認！Eremium 専用�E�E|
| `debug/race/[race_id]/features` | 特徴量確認！Eremium 専用�E�E|
| `debug/race-ids` | レース ID 一覧チE��チE�� |

#### `/src/components/`  E共送EUI コンポ�EネンチE

| ファイル | 役割 |
|---|---|
| `RaceFeaturePanel.tsx` | 特徴量テーブルパネル�E��Eフィルタ・ソート付き�E�E|
| `RacePredictionPanel.tsx` | 予測結果パネル�E�確玁E�EEV・推奨馬券表示�E�E|
| `AdminOnly.tsx` | 管琁E��E�Eみ表示するラチE��ーコンポ�EネンチE|
| `ConfirmDialog.tsx` | 確認ダイアログ�E�削除等�E操作前確認！E|
| `ErrorBoundary.tsx` | エラーバウンダリ�E�クラチE��ュ防止�E�E|
| `InstallPWA.tsx` | PWA インスト�Eルボタン |
| `Logo.tsx` | アプリロゴコンポ�EネンチE|
| `Toast.tsx` | ト�Eスト通知 |

#### `/src/hooks/`  Eカスタムフック

| ファイル | 役割 |
|---|---|
| `useUserRole.ts` | ログインユーザーの role�E�Edmin/user�E�取征E|
| `useScrape.ts` | スクレイピングジョブ�E開始�E進捗管琁E|
| `useBatchScrape.ts` | 褁E��日のバッチスクレイプ管琁E|
| `useJobPoller.ts` | ジョチEID を�Eーリングして進捗を返す汎用フック |
| `useRaceCache.ts` | レースチE�EタのキャチE��ュ管琁E|

#### `/src/contexts/`  EReact コンチE��スチE

| ファイル | 役割 |
|---|---|
| `AuthContext.tsx` | Supabase 認証状態�E全体�E朁E|
| `UltimateModeContext.tsx` | Ultimate 版モード！E0 列特徴量）�E ON/OFF 状態�E朁E|

#### `/src/lib/`  E共通ライブラリ・型定義

| ファイル | 役割 |
|---|---|
| `backend-url.ts` | FastAPI エンド�EインチEURL の一允E��琁E��EML_API_URL`, `SCRAPE_SERVICE_URL`�E�E|
| `supabase.ts` | Supabase クライアント�E期化�E�認証・ユーザー DB�E�E|
| `betting-strategy.ts` | ケリー基準による賭け額計算（フロントエンド�E�E�E|
| `stripe.ts` | Stripe クライアント�E期化 |
| `race-analysis-types.ts` | レース刁E��レスポンスの TypeScript 型定義 |
| `types.ts` | 共通型定義 |

---

### `/python-api/`  EFastAPI ML バックエンチE

#### エントリポイント�E設宁E

| ファイル | 役割 |
|---|---|
| `main.py` | **FastAPI エントリポインチE*。�Eルーターを登録し、サーバ�Eを起勁E|
| `app_config.py` | 共有設定�Eルパ�E�E�EULTIMATE_DB`, `get_latest_model()`, `load_model_bundle()` など�E�E|
| `models.py` | Pydantic リクエスチEレスポンスモチE���E�バリチE�Eション付き�E�E|
| `betting_strategy.py` | ケリー基準�E期征E��計算�E購入推奨ロジチE���E�Eython 版！E|
| `scheduler.py` | 定期学習�Eスクレイピングの自動スケジューラー |
| `supabase_client.py` | Supabase 接続クライアント（ユーザー惁E��取得！E|
| `requirements.txt` | Python 依存パチE��ージ一覧 |
| `runtime.txt` | Python バ�Eジョン持E��！Eender チE�Eロイ用�E�E|
| `start.bat` | Windows 用起動バチE�� |

#### `/python-api/routers/`  EAPI ルーター群

| ファイル | 役割 |
|---|---|
| `scrape.py` | スクレイピングジョブ管琁E��開始�EスチE�Eタス確認�E中断�E�E|
| `races.py` | レースチE�Eタ取得（日付別・最新・馬一覧�E�E|
| `train.py` | モチE��学習（非同期ジョブ、E��捗�Eーリング対応！E|
| `predict.py` | レース刁E��・予測�E�単一 / バッチE��E|
| `models_mgmt.py` | 保存済みモチE��の一覧・詳細・削除 |
| `purchase.py` | 購入推奨の保存�E取得�E更新 |
| `stats.py` | 皁E��玁E�E収支統計集訁E|
| `realtime_odds.py` | リアルタイムオチE��取得�EキャチE��ュ |
| `export.py` | チE�Eタ・馬券の CSV エクスポ�EチE|
| `backfill.py` | 欠損データの補完（毛色・血統�E�E|
| `debug_data.py` | チE��チE��用生データ・特徴量確認！Eremium 専用�E�E|
| `internal.py` | 冁E��ユーチE��リチE���E�Edmin 専用�E�E|
| `profiling.py` | チE�Eタプロファイリングレポ�Eト生戁E|
| `bet_export.py` | 馬券リスト�Eエクスポ�EチE|

#### `/python-api/scraping/`  Eスクレイピング処琁E

| ファイル | 役割 |
|---|---|
| `race.py` | **メインスクレイパ�E**�E��E馬表・レース結果を取得して DB 保存！E|
| `horse.py` | 馬詳細惁E���E�馬名�E血統・馬齢�E��EスクレイチE|
| `storage.py` | スクレイプ結果めE`keiba_ultimate.db` へ書き込み |
| `jobs.py` | スクレイピングジョブ�E状態管琁E��スレチE��セーフ！E|
| `constants.py` | スクレイピング用定数�E�会場コード�E馬場種別など�E�E|

#### `/python-api/middleware/`  Eミドルウェア

| ファイル | 役割 |
|---|---|
| `auth.py` | Supabase JWT 検証・ユーザーロール確認！Eree/Premium/Admin�E�E|

#### `/python-api/models/`  E学習済みモチE��置き場

| パス | 役割 |
|---|---|
| `model_win_lightgbm_*_ultimate.joblib` | **現行モチE��**�E�Eget_latest_model()`が�E動選択！E|
| `archive/` | 旧世代モチE��のアーカイブ（参照・ロールバック用�E�E|

#### `/python-api/tests/`  EバックエンドテスチE

| ファイル | 役割 |
|---|---|
| `test_endpoints.py` | API エンド�Eイント�E統合テスチE|

#### ルート直下�E運用ユーチE��リチE��

| ファイル | 役割 |
|---|---|
| `check_db_races.py` | DB 冁E��ースチE�Eタの整合性確誁E|
| `repair_races.py` | 壊れたレースチE�Eタの修復 |
| `e2e_verify.py` | FastAPI エンド�Eイント�E E2E 疎通確誁E|
| `run_scrape.py` | スクレイピングのバッチ実行スクリプト |
| `run_test_scrape.py` | スクレイピングのチE��チE��実衁E|
| `show_issues.py` | バリチE�Eション問題�E表示 |
| `validate_api.py` | API 動作確認ツール |
| `verify_pipeline.py` | パイプライン全体�E疎通確誁E|
| `execute_vote.py` | 自動投票 CLI�E�EPAT 連携�E�E|
| `ipat_voter.py` | IPAT API 自動投票実裁E|

---

### `/keiba/`  Eコア ML ライブラリ

#### `/keiba/keiba_ai/`  EPython パッケージ本佁E

| ファイル | 役割 |
|---|---|
| `config.py` | 設定クラス�E�EAppConfig`, `NetkeibaConfig`�E�E|
| `constants.py` | 共通定数�E�EFUTURE_FIELDS`, `UNNECESSARY_COLUMNS`, `ID_COLUMNS`�E�E|
| `db.py` | 旧牁EDB 操作！Eeiba.db 対応！E|
| `db_ultimate.py` | Ultimate 牁EDB 操作！Eeiba_ultimate.db 対応！E|
| `db_ultimate_loader.py` | 学習用 DataFrame ロード！E3 刁EJSON ↁEDataFrame�E�E|
| `schema_ultimate.sql` | DB スキーマ定義�E�Eraces_ultimate` / `race_results_ultimate`�E�E|
| `feature_engineering.py` | **派生特徴量計箁E*�E�前走・体重・コーナ�E・オチE��系、データリーク防止済み�E�E|
| `ultimate_features.py` | **Ultimate 版特徴釁E*�E�過去 10 走統計�E騎手/調教師 180 日統計！E|
| `lightgbm_feature_optimizer.py` | LightGBM 用前�E琁E��Eabel Encoding・未来惁E��除外！E|
| `optuna_optimizer.py` | Optuna によるハイパ�Eパラメータ最適化！E00 試行�E5-fold CV�E�E|
| `optuna_all_models.py` | 褁E��モチE���E�EightGBM/RF/XGBoost�E��E Optuna 最適匁E|
| `quality_gate.py` | チE�Eタ品質ゲート（距離 0・重褁E��番・オチE��全 None チェチE���E�E|
| `predict.py` | 予測 CLI�E�Epython -m keiba_ai.predict` 用�E�E|
| `ingest.py` | チE�Eタ収集メイン�E�スクレイピング ↁEDB 保存�Eオーケストレーション�E�E|
| `pipeline_daily.py` | 日次パイプライン�E�収雁EↁE学習を頁E��実行！E|
| `train.py` | LogisticRegression 学習（旧版�E比輁E���E�E|
| `utils.py` | 共通ユーチE��リチE���E�EST 変換・日付操作！E|
| `extract_odds.py` | オチE��チE�Eタ抽出処琁E|
| `course_master.yaml` | コースマスタチE�Eタ�E�直線長・コーナ�E・特性�E�E|
| `MODULES.md` | モジュール構�Eの説明書 |

#### `/keiba/keiba_ai/netkeiba/`  Eスクレイピングモジュール

| ファイル | 役割 |
|---|---|
| `client.py` | **通常 HTTP スクレイパ�E**�E�Eequests、E.5、E.5 秒征E��！E|
| `browser_client.py` | **ブラウザ自動化スクレイパ�E**�E�Elaywright、IP 規制回避・動的ペ�Eジ対応！E|
| `parsers.py` | HTML 解析（�E馬表・レース結果めEDataFrame に変換�E�E|

#### `/keiba/data/`  EチE�Eタファイル

| パス | 役割 |
|---|---|
| `keiba_ultimate.db` | **本番 SQLite DB**�E��Eレース・馬・結果チE�Eタ�E�E|
| `keiba.db` | 標準版 DB�E�Eltimate モード無効時�Eフォールバック�E�E|
| `keiba_local_validate.db` | バリチE�Eション専用 DB�E�開発・チE��ト用�E�E|
| `html/` | HTTP スクレイプ時の HTML キャチE��ュ |
| `html_rendered/` | ブラウザスクレイプ時の HTML キャチE��ュ |
| `models/` | keiba 側モチE��保管�E�旧版！E|
| `logs/` | スクレイピングログ |

---

### `/e2e/`  EE2E チE��ト！Elaywright�E�E

| ファイル | 役割 |
|---|---|
| `dashboard.spec.ts` | ダチE��ュボ�Eド画面の E2E チE��チE|
| `data-collection.spec.ts` | チE�Eタ収集画面�E�スクレイプ起動�E進捗�EIP ブロチE��警告！E|
| `data-view.spec.ts` | チE�Eタ閲覧画面の E2E チE��チE|
| `home.spec.ts` | ホ�Eム画面の E2E チE��チE|
| `predict-batch.spec.ts` | 一括予測画面の E2E チE��チE|
| `race-analysis.spec.ts` | レース刁E��画面の E2E チE��ト（特徴量パネルのカラム数表示�E�E|
| `train.spec.ts` | 学習画面の E2E チE��チE|

---

### `/tools/`  EDB・チE�Eタ運用チE�Eル

本番 DB の補完�E診断・再学習などのメンチE��ンスに使ぁE��クリプト群、E

**DB 診断**

| ファイル | 役割 |
|---|---|
| `check_dbs.py` | DB ファイル一覧とレコード数確誁E|
| `inspect_db.py` | フィールド別允E��玁E�E詳細確誁E|
| `check_missing.py` | 欠損フィールドチェチE�� |
| `run_local_pipeline.py` | ローカルパイプライン実行テスチE|
| `leakage_audit.py` | チE�Eタリーク監査�E�特徴量�E相関検証�E�E|
| `leakage_audit2.py` | チE�Eタリーク監査・詳細版！Eit_transform 経路の検証�E�E|

**DB 修復・補宁E*

| ファイル | 役割 |
|---|---|
| `repair_db.py` | DB 修復ユーチE��リチE���E�整合性エラー修正�E�E|
| `patch_horse_names.py` | 馬名データの表記ゆれ修正 |
| `rescrape_horse_stats.py` | 馬統計�E再スクレイチE|
| `rescrape_dates.py` | 持E��日のチE�Eタを�EスクレイチE|
| `rescrape_dates_playwright.py` | ブラウザ経由での再スクレイプ！EP 規制回避�E�E|
| `fix_distance_zero.py` | distance=0 の異常チE�Eタ修正 |
| `fix_dates_calendar.py` | 日付データの補正 |
| `audit_fixes.py` | 修正前後�EチE�Eタ差刁E��誁E|

**再学習�Eパイプライン検証**

| ファイル | 役割 |
|---|---|
| `retrain_local.py` | ローカル再学習（モチE��差し替え検証用�E�E|
| `verify_pipeline_full.py` | フルパイプライン検証�E�スクレイチEↁEFE ↁE推論！E|
| `scrape_and_validate.py` | スクレイチE+ バリチE�Eションの連続実衁E|
| `e2e_operational_verify.py` | 本番 API への E2E 疎通確誁E|
| `e2e_verify_timesplit.py` | 時系列�E割した評価検証 |
| `audit_scraping_quality.py` | スクレイプデータの品質監査 |
| `verify_scraping.py` | スクレイプ結果の整合性確誁E|
| `drift_report.py` | チE�Eタドリフトレポ�Eト生戁E|
| `debug_regen.py` | 再学習デバッグ用スクリプト |
| `generate_holdout_output.py` | ホ�Eルドアウト評価の出力生戁E|
| `generate_validation_output.py` | バリチE�Eション出力生戁E|
| `regen_pipeline_output.py` | パイプライン出力�E再生戁E|

---

### `/validation/`  EチE�Eタ品質・特徴量検証

開発・チE��チE��・品質確認�Eためのスクリプト群�E�本番操作なし）、E

| ファイル | 役割 | 実行タイミング |
|---|---|---|
| `check_null_rates3.py` | DB 全 JSON キーの允E��玁E��誁E| パッチ実行後�E再学習前 |
| `check_date_leakage.py` | チE�Eタリーク診断�E�未来日付�E混入チェチE���E�E| 特徴量修正征E|
| `check_features_detail.py` | 生特徴釁Evs モチE��入力�E詳細比輁E��型・刁E��E��E| FE 変更征E|
| `check_final_features.py` | 最終モチE��入力特徴量�Eカラム一覧確誁E| モチE��再学習前 |

---

### `/scripts/`  E起動�E運用スクリプト

| ファイル | 役割 |
|---|---|
| `start-all.ps1` / `start-all.bat` | FastAPI + Next.js を一括起勁E|
| `start-dev.ps1` / `start-dev.bat` | 開発モードで起動！Eext.js のみ�E�E|
| `stop-all.ps1` | 全サーバ�E停止�E�Enpm run down` から呼ばれる�E�E|
| `check_server.ps1` | サーバ�E稼働状態�E確誁E|
| `create-desktop-shortcut.ps1` | チE��クトップショートカチE��作�E |
| `start_playwright_server.ps1` | Playwright ブラウザサーバ�E起動！E2E チE��ト前�E�E|
| `stop_playwright_server.ps1` | Playwright ブラウザサーバ�E停止 |

---

### `/supabase/`  ESupabase DB スキーチE

> Supabase は認証・ユーザー管琁E��。レース予測チE�Eタは SQLite�E�Ekeiba_ultimate.db`�E�を使用、E

| ファイル | 役割 |
|---|---|
| `schema.sql` | ユーザー・購入履歴チE�Eブル定義�E��E回セチE��アチE�E時に適用�E�E|
| `schema_ultimate.sql` | Ultimate 版スキーチE|
| `race_schema.sql` | レース惁E��チE�Eブル�E�Eupabase 側�E�E|
| `setup_admin.sql` | 管琁E��E��ーザーの初期設宁E|
| `setup_scraping_tables.sql` | スクレイピング管琁E��ーブルの初期設宁E|
| `clear_users.sql` | ユーザーチE�Eタ全削除�E�開発用�E�E|
| `migrations/` | DB マイグレーションファイル |

---

### `/docs/`  EドキュメンチE

| パス | 役割 |
|---|---|
| `README.md` | ドキュメント�E索弁E|
| `PROJECT_OVERVIEW.md` | **こ�Eファイル**。�E体構�E・役割一覧 |
| `DEV_WORKFLOW.md` | 開発ワークフロー�E�データ収集→学習�E予測の手頁E��E|
| `IMPLEMENTATION_FLOW.md` | 実裁E��ローの詳細 |
| `AUTO_TRAIN_DESIGN.md` | 自動学習シスチE��の設計仕槁E|
| `scraping-flow.md` | スクレイピングアーキチE��チャ |
| `deployment/DEPLOYMENT_COMPLETE_GUIDE.md` | Vercel + Railway チE�Eロイ完�EガイチE|
| `development/` | 開発老E��け技術ドキュメント（スクレイピング・DB・LightGBM 等！E|
| `features/` | 機�E仕様書・API 仕様�E特徴量ガイチE|
| `setup/` | セチE��アチE�Eガイド（�E回�E管琁E��E�Eロール設定！E|
| `reports/` | 実裁E��ポ�Eト�E最適化実績レポ�EチE|

---

### `/public/`  EPWA アセチE��

| ファイル | 役割 |
|---|---|
| `manifest.json` | PWA マニフェスト（アプリ名�Eアイコン・表示設定！E|
| `sw.js` | Service Worker�E�オフライン対応�EキャチE��ュ�E�E|
| `unregister-sw.html` | Service Worker 強制解除ペ�Eジ�E�デバッグ用�E�E|

---

### ルート直下�E主要ファイル

**Python スクリプト**

| ファイル | 役割 |
|---|---|
| `patch_missing_data.py` | **DB チE�Eタ補完ツール**�E�欠損フィールドを netkeiba から補完、ルート固定！E|
| `generate_feature_report.py` | 特徴量重要度 HTML レポ�Eト生戁E|
| `generate_profiling_report.py` | チE�Eタプロファイリング HTML レポ�Eト生戁E|

**設定ファイル**

| ファイル | 役割 |
|---|---|
| `package.json` | npm 依存パチE��ージ・スクリプト定義 |
| `next.config.js` | Next.js 設宁E|
| `tailwind.config.ts` | Tailwind CSS 設宁E|
| `tsconfig.json` | TypeScript 設宁E|
| `vitest.config.ts` | Vitest 設定（フロントエンドユニットテスト！E|
| `playwright.config.ts` | Playwright 設定！E2E チE��ト！E|
| `pytest.ini` | pytest 設定！Eython ユニットテスト！E|

**チE�Eロイ設宁E*

| ファイル | 役割 |
|---|---|
| `Dockerfile` | Docker イメージ定義�E�ローカル / セルフ�Eスト！E|
| `Procfile` | Railway プロセス定義 |
| `railway.json` | Railway チE�Eロイ設宁E|
| `render.yaml` | Render チE�Eロイ設定！Eailway 非使用時�E代替�E�E|
| `nixpacks.toml` | Nixpacks ビルド設定！Eailway�E�E|

**環墁E��数**

| ファイル | 役割 |
|---|---|
| `.env.local` | ローカル環墁E��数�E�Eit 管琁E��！E|
| `.env.local.example` | ローカル環墁E��数のサンプル |
| `.env.example` | 一般皁E��環墁E��数サンプル |
| `.env.production.template` | 本番環墁E��数チE��プレート！Eailway / Vercel 設定時に参�E�E�E|

---

## チE�Eタの流れ�E�予測まで�E�E

```
① netkeiba.com
      ━Escraping/race.py�E�ETTP / Playwright�E�E
      ▼
② keiba/data/keiba_ultimate.db�E�EQLite3�E�E
      ━Eraces_ultimate / race_results_ultimate
      ▼
③ keiba_ai/db_ultimate_loader.py
      ━EJSON ↁEDataFrame�E�E3 列！E
      ▼
④ keiba_ai/feature_engineering.py
      ━E派生特徴量追加�E�E40 刁EↁE合訁E~125 列！E
      ▼
⑤ keiba_ai/ultimate_features.py
      ━E過去統計追加�E�E24 刁EↁE合訁E~137 列！E
      ▼
⑥ routers/train.py  POST /api/train/start
      ━ELightGBM + Optuna�E�E00 試行�E5-fold CV�E��E .joblib 保孁E
      ▼
⑦ python-api/models/model_win_*_ultimate.joblib
      ▼
⑧ routers/predict.py  POST /api/analyze_race
      ━E特徴量生戁EↁEpredict_proba ↁEキャリブレーション
      ━EↁEケリー基準で賭け額計箁EↁE購入推奨 JSON
      ▼
⑨ Next.js  /predict-batch / /race-analysis
       ユーザーへ結果表示
```

---

## チE��ト構�E

| スイーチE| コマンチE| 対象 |
|---|---|---|
| Python pytest | `.venv\Scripts\python.exe -m pytest keiba/keiba_ai/tests/` | ML パイプライン・特徴釁E|
| Vitest | `npm test` | フロントエンドロジチE���E��EチE��ィング計算等！E|
| Playwright | `npx playwright test` | 全画面の E2E |

---
