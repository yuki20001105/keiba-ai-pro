# KEIBA-AI-PRO プロジェクト全体構成

> 現在日: 2026-02-21

---

## システム全体像

```
ブラウザ（ユーザー）
    │
    ▼
┌─────────────────────────────────┐
│  Next.js  (src/)  :3000         │  ← フロントエンド + Next.js API Routes
│  TypeScript / React             │
└────────────┬────────────────────┘
             │ HTTP
             ▼
┌─────────────────────────────────┐
│  FastAPI  (python-api/)  :8000  │  ← 機械学習バックエンド
│  Python / LightGBM              │
└────────────┬────────────────────┘
             │ import
             ▼
┌─────────────────────────────────┐
│  keiba_ai  (keiba/keiba_ai/)    │  ← コアMLライブラリ
│  スクレイピング / 特徴量 / 学習  │
└────────────┬────────────────────┘
             │
             ▼
┌─────────────────────────────────┐
│  SQLite DB (keiba/data/)        │  ← 本番データベース
│  keiba_ultimate.db              │
└─────────────────────────────────┘
```

---

## ディレクトリ別 用途一覧

---

### `/src/` — Next.js フロントエンド

#### `/src/app/` — ページ

| パス | 用途 |
|---|---|
| `page.tsx` | トップページ（ルートリダイレクト） |
| `layout.tsx` | 全ページ共通レイアウト（ヘッダー・フッター・PWA設定） |
| `home/` | ホーム画面 |
| `dashboard/` | ダッシュボード（予測結果・統計一覧） |
| `predict-batch/` | 一括予測ページ（レースを選んでまとめて予測） |
| `train/` | モデル学習ページ（手動学習トリガー） |
| `data-collection/` | データ収集ページ（スクレイピング操作UI） |
| `admin/` | 管理者専用ページ（ユーザー管理・システム設定） |
| `auth/` | 認証ページ（ログイン・サインアップ） |

#### `/src/app/api/` — Next.js API Routes（プロキシ・軽量処理）

| パス | 用途 |
|---|---|
| `ml/train/route.ts` | FastAPI `/api/train` へのプロキシ |
| `ml/predict/route.ts` | FastAPI `/api/predict` へのプロキシ |
| `ml/models/route.ts` | FastAPI `/api/models` へのプロキシ（モデル一覧） |
| `predict/route.ts` | 予測リクエストの受け口 |
| `netkeiba/race-list/` | 指定日のレースID一覧取得（FastAPI経由） |
| `netkeiba/race/` | 個別レース詳細取得 |
| `netkeiba/calendar/` | レースカレンダー取得 |
| `ocr/route.ts` | **Google Vision API** で馬券画像をOCR読み取り |
| `ai-correct/route.ts` | **OpenAI / Gemini** でOCR結果を補正 |
| `stripe/create-checkout/` | Stripe 決済セッション作成（課金機能） |
| `stripe/portal/` | Stripe カスタマーポータル（課金管理） |
| `stripe/webhook/` | Stripe Webhook 受信（支払い完了通知） |

#### `/src/lib/` — フロントエンド共通ライブラリ

| ファイル | 用途 |
|---|---|
| `supabase.ts` | Supabase クライアント（認証・ユーザーDB） |
| `keiba-ai.ts` | FastAPI への通信ヘルパー関数群 |
| `betting-strategy.ts` | ケリー基準による賭け額計算（フロントエンド側） |
| `stripe.ts` | Stripe クライアント初期化 |

#### `/src/hooks/` — カスタムフック

| ファイル | 用途 |
|---|---|
| `useUserRole.ts` | ログインユーザーの role（admin/user）を取得 |

#### `/src/contexts/` — Reactコンテキスト

| ファイル | 用途 |
|---|---|
| `UltimateModeContext.tsx` | Ultimate版モード（90列特徴量）のON/OFF状態を全コンポーネントで共有 |

#### `/src/components/` — 共通UIコンポーネント

| ファイル | 用途 |
|---|---|
| `AdminOnly.tsx` | 管理者のみ表示するラッパーコンポーネント |
| `InstallPWA.tsx` | PWAインストールボタン |

---

### `/python-api/` — FastAPI 機械学習バックエンド

| ファイル | 用途 |
|---|---|
| `main.py` | **本番サーバー本体**（2600行）。全MLエンドポイントを定義 |
| `betting_strategy.py` | ケリー基準・期待値計算・購入推奨ロジック（Python版） |
| `requirements.txt` | Python依存パッケージ一覧 |
| `start.bat` | Windows用 起動スクリプト |
| `models/` | 学習済みモデルファイル置き場（.joblib） |
| `models/archive/` | 旧モデルのアーカイブ（参照用） |

#### FastAPI の主要エンドポイント

| エンドポイント | 用途 |
|---|---|
| `GET  /` | ヘルスチェック |
| `GET  /api/data_stats` | DBの統計情報（レース数・馬数・モデル数） |
| `POST /api/train` | モデル学習（LightGBM/LogisticRegression） |
| `POST /api/predict` | 予測実行 |
| `GET  /api/models` | 学習済みモデル一覧 |
| `POST /api/analyze-race` | レース分析＋購入推奨（ケリー基準） |
| `POST /api/purchase-history` | 購入履歴の保存 |

---

### `/keiba/` — コアMLライブラリ（本番稼働の心臓部）

#### `/keiba/keiba_ai/` — Python パッケージ本体

| ファイル | 用途 |
|---|---|
| `__init__.py` | パッケージ初期化 |
| `__main__.py` | `python -m keiba_ai` 実行エントリポイント |
| `config.py` | 設定クラス（`AppConfig`, `NetkeibaConfig` 等） |
| `config.yaml` | 設定ファイル（スクレイピング間隔・DB path 等） → ルートの `keiba/` にある |
| `db.py` | 旧版DB操作（keiba.db 対応） |
| `db_ultimate.py` | Ultimate版DB操作（keiba_ultimate.db 対応） |
| `db_ultimate_loader.py` | 学習用データフレームのロード（90列特徴量対応） |
| `schema_ultimate.sql` | DBスキーマ定義（本番テーブル全定義） |
| `ingest.py` | **データ収集メイン**（スクレイピング→DB保存） |
| `pipeline_daily.py` | 日次パイプライン（収集→学習を順番に実行） |
| `train.py` | LogisticRegression 学習（`_make_target` 関数を main.py で利用） |
| `predict.py` | 予測 CLI（`python -m keiba_ai.predict` 用） |
| `feature_engineering.py` | 派生特徴量の計算（展開窓方式・データリーク修正済み） |
| `lightgbm_feature_optimizer.py` | LightGBM 用特徴量前処理・エンコーディング |
| `ultimate_features.py` | Ultimate版 90列特徴量の計算クラス |
| `optuna_optimizer.py` | Optuna によるハイパーパラメータ最適化（LightGBM） |
| `optuna_all_models.py` | 複数モデル（LightGBM/RF/XGBoost）をOptuna最適化 |
| `utils.py` | 共通ユーティリティ（JST変換・日付操作 等） |
| `extract_odds.py` | オッズデータの抽出処理 |
| `course_master.yaml` | コースマスタデータ（直線長・コーナー等） |
| `MODULES.md` | モジュール構成の説明書 |

#### `/keiba/keiba_ai/netkeiba/` — スクレイピングモジュール

| ファイル | 用途 |
|---|---|
| `client.py` | netkeiba.com 通常HTTPスクレイパー（requests使用、2.5〜3.5秒待機） |
| `browser_client.py` | ブラウザ自動化スクレイパー（Playwright / Selenium、IP規制回避） |
| `parsers.py` | HTML解析（出馬表・レース結果・馬詳細をDataFrameに変換） |

#### `/keiba/data/` — データファイル

| パス | 用途 |
|---|---|
| `keiba_ultimate.db` | **本番SQLiteデータベース**（全レース・馬・結果データ） |
| `keiba.db` | 標準版モード用DB（Ultimateモード無効時のフォールバック先） |
| `html/` | スクレイピングHTMLキャッシュ（通常モード） |
| `html_rendered/` | スクレイピングHTMLキャッシュ（ブラウザモード） |
| `netkeiba/` | スクレイピング中間データ |
| `logs/` | スクレイピングログ |
| `models/` | 旧モデル保管場所（keiba側） |
| `tmp_config_ui.yaml` | UIから変更した設定の一時ファイル |

---

### `/supabase/` — Supabase（クラウドユーザーDB）

| ファイル | 用途 |
|---|---|
| `schema.sql` | ユーザー・プロフィール・購入履歴テーブル定義 |
| `schema_ultimate.sql` | Ultimate版スキーマ |
| `race_schema.sql` | レース情報テーブル（Supabase側） |
| `setup_admin.sql` | 管理者ユーザーの初期設定 |
| `setup_scraping_tables.sql` | スクレイピング管理テーブルの初期設定 |
| `clear_users.sql` | ユーザーデータ全削除スクリプト（開発用） |

> **注意**: Supabase は認証・ユーザー管理用。レース予測データは SQLite（`keiba_ultimate.db`）を使用。

---

### `/validation/` — 検証スクリプト ★パッチ完了後に使用

| ファイル | 用途 |
|---|---|
| `check_null_rates3.py` | DB充填率確認（各カラムのNULL率を表示） |
| `check_date_leakage.py` | データリーク診断（未来日付の混入チェック） |
| `check_features_detail.py` | 特徴量の詳細確認（型・分布） |
| `check_final_features.py` | 最終特徴量一覧の確認 |
| `README.md` | 各スクリプトの使い方 |

---

### `/tools/` — 運用補助ツール

| パス | 用途 |
|---|---|
| `README.md` | `patch_missing_data.py` の使い方・フェーズ説明 |
| `logs/` | パッチ実行ログの保存場所 |

---

### `/scripts/` — サーバー起動・停止スクリプト

| ファイル | 用途 |
|---|---|
| `start-all.ps1` / `start-all.bat` | FastAPI + Next.js を一括起動 |
| `start-dev.ps1` / `start-dev.bat` | 開発用一括起動 |
| `stop-all.ps1` | 全サーバー停止 |
| `check_server.ps1` | サーバー稼働状態確認 |
| `create-desktop-shortcut.ps1` | デスクトップショートカット作成 |
| `start_playwright_server.ps1` | Playwright ブラウザサーバー起動 |
| `stop_playwright_server.ps1` | Playwright ブラウザサーバー停止 |

---

### `/docs/` — ドキュメント

| パス | 用途 |
|---|---|
| `AUTO_TRAIN_DESIGN.md` | 自動学習システム設計仕様書（次回実装予定） |
| `README.md` | docs ディレクトリの索引 |
| `deployment/` | デプロイ手順（Railway / Vercel） |
| `development/` | 開発環境構築手順 |
| `features/` | 機能仕様書 |
| `setup/` | セットアップガイド |
| `reports/` | 実装レポート・最適化レポート |

---

### `/public/` — PWA アセット

| ファイル | 用途 |
|---|---|
| `manifest.json` | PWAマニフェスト（アプリ名・アイコン・表示設定） |
| `sw.js` | Service Worker（オフライン対応・キャッシュ） |
| `icon-192x192.png` / `icon-512x512.png` | PWAアイコン |
| `unregister-sw.html` | Service Worker 強制解除ページ（デバッグ用） |

---

### ルート直下の主要ファイル

| ファイル | 用途 |
|---|---|
| `package.json` | Next.js 依存パッケージ管理 |
| `next.config.js` | Next.js 設定（API プロキシ先 等） |
| `tailwind.config.ts` | Tailwind CSS 設定 |
| `tsconfig.json` | TypeScript 設定 |
| `Procfile` | Railway デプロイ用プロセス定義 |
| `railway.json` | Railway デプロイ設定 |
| `QUICKSTART.md` | 環境構築の最短手順 |
| `README.md` | プロジェクト概要 |
| `.env.local` | 環境変数（Supabase URL/KEY・Stripe KEY 等、Git管理外） |
| `.env.local.example` | 環境変数のサンプル |
| `.env.production.template` | 本番環境変数テンプレート |
| `patch_missing_data.py` | **現在実行中**のデータ補完スクリプト（完了後に削除予定） |
| `patch_log.txt` | パッチ実行ログ（完了後に削除予定） |


---

## 技術スタック早見表

| レイヤー | 技術 | 用途 |
|---|---|---|
| フロントエンド | Next.js 14 / TypeScript / Tailwind CSS | WebUI・API Routes |
| MLバックエンド | FastAPI / Python 3.11 | 学習・予測API |
| ML本体 | LightGBM / scikit-learn / Optuna | モデル学習・最適化 |
| スクレイピング | requests / Playwright / Selenium | データ収集 |
| ローカルDB | SQLite (WALモード) | レース・予測データ |
| クラウドDB | Supabase (PostgreSQL) | ユーザー認証・課金管理 |
| 課金 | Stripe | 有料プラン |
| AI補助 | OpenAI GPT / Google Gemini | OCR補正 |
| OCR | Google Vision API | 馬券画像読み取り |
| PWA | Service Worker | モバイルアプリ化 |
| デプロイ | Railway (FastAPI) / Vercel (Next.js) | 本番環境 |

---

## データの流れ（予測まで）

```
① netkeiba.com
      │ スクレイピング（client.py / browser_client.py）
      ▼
② keiba_ultimate.db
      │ SQLite（races_ultimate / race_results_ultimate）
      ▼
③ db_ultimate_loader.py
      │ 学習用DataFrame生成
      ▼
④ feature_engineering.py / ultimate_features.py
      │ 90列特徴量計算（expanding window、データリーク防止済み）
      ▼
⑤ python-api/main.py  POST /api/train
      │ LightGBM CV最適化 → モデル保存（.joblib）
      ▼
⑥ python-api/models/
      │ model_win_lightgbm_YYYYMMDD_HHMMSS_optimized_ultimate.joblib
      ▼
⑦ python-api/main.py  POST /api/analyze-race
      │ 出走表を読み込み → 特徴量生成 → 確率予測 → ケリー基準で賭け額計算
      ▼
⑧ Next.js  /dashboard  /predict-batch
      ユーザーへ結果表示
```

---

*最終更新: 2026-02-21*
