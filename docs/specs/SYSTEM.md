# keiba-ai-pro システム仕様書（SYSTEM SPEC）

> **目的**: GitHub Copilot などの AI への指示精度を上げるための「変更してはならない前提・契約」を定義する。  
> **更新方針**: Spec 変更は慎重に行う。実装・リファクタリングのたびに変えるものではない。

---

## 1. アーキテクチャ概要（Architecture）

```
ブラウザ（ユーザー）
    ↓ HTTP
┌──────────────────────────────────────┐
│  Next.js  (src/)  :3000              │ ← UI + API プロキシ層
│  TypeScript / React / Tailwind CSS   │
└──────────────┬───────────────────────┘
               ↓ HTTP (localhost:8000)
┌──────────────────────────────────────┐
│  FastAPI  (python-api/)  :8000       │ ← ML バックエンド
│  routers/ + scraping/ + models/      │
└──────────────┬───────────────────────┘
               ↓ Python import
┌──────────────────────────────────────┐
│  keiba_ai  (keiba/keiba_ai/)         │ ← コア ML ライブラリ
│  特徴量エンジニアリング / LightGBM   │
└──────────────┬───────────────────────┘
               ↓ SQLite3
┌──────────────────────────────────────┐
│  keiba_ultimate.db (keiba/data/)     │ ← ローカル本番 DB
│  14,000+ レース / 180,000+ 馬データ  │
└──────────────────────────────────────┘
               ↕ ( + Supabase PostgreSQL: 認証・購入記録のみ )
```

---

## 2. 技術スタック（Tech Stack）

| レイヤー | 技術 | ポート / パス | 備考 |
|---|---|---|---|
| フロントエンド | Next.js 16 / TypeScript / Tailwind CSS | :3000 | App Router |
| ML バックエンド | FastAPI / Python 3.11 / uvicorn | :8000 | 単一プロセス |
| ML エンジン | LightGBM / scikit-learn / Optuna | — | joblib 保存 |
| ローカル DB | SQLite3 WAL モード | `keiba/data/keiba_ultimate.db` | 主データストア |
| クラウド DB | Supabase (PostgreSQL) | — | 認証・購入記録のみ |
| スクレイピング | aiohttp / httpx / BeautifulSoup | — | netkeiba.com |
| 認証 | Supabase JWT | — | Bearer token |

---

## 3. 不変条件（Invariants）— ここが最重要

> **AI への指示**: 以下の不変条件を満たさない変更は絶対に行わないこと。

### INV-01: 予測パイプライン順序

```
While predict endpoint is called,
when race_id が渡される,
the system shall 以下の順序で処理する:
  1. keiba_ultimate.db から race_results_ultimate を読む
  2. 過去全履歴 DataFrame をキャッシュ付きで結合
  3. keiba_ai の feature_engineering でカラムを追加
  4. POST_RACE_FIELDS（着順・タイム等）を必ず除外
  5. LightGBM model.predict() で確率スコア算出
  6. BettingRecommender で期待値・推奨購入を生成
```

**禁止事項**: 手順 4（未来情報除外）の省略、手順の入れ替え

---

### INV-02: オッズ取得の優先順位

```
When odds を予測レスポンスに含める,
the system shall 以下の優先順位で取得する:
  1. df_pred（再スクレイプ後の DataFrame）の odds カラム（horse_number をキーに逆引き）
  2. _horse_records の odds フィールド（DB格納値）
  3. 再スクレイプを自動実行して取得（条件: 全馬 odds=0 or NaN）
  4. 取得不能の場合のみ null を返す（0.0 は null と同等に扱う）
```

**禁止事項**: `_hr.get("odds") or ...` のような `or` 演算子（0.0 を falsy 誤判定するため）

---

### INV-03: 再スクレイプの日付判定

```
When odds 再スクレイプを実行する,
the system shall race_date <= today の場合は結果ページ（確定オッズ）を使用する。
the system shall race_date > today の場合は出馬表ページ（暫定オッズ）を使用する。
the system shall 結果ページにオッズがない（レース未了）場合は出馬表にフォールバックする。
```

**禁止事項**: `race_date < today`（`<` ではなく `<=`）

---

### INV-04: 並列処理制限

```
While UI から一括予測を実行する,
when 複数レースを処理する,
the system shall CONCURRENCY = 1 で逐次処理する。
```

**理由**: FastAPI は単一 Python プロセス。GIL 競合により 4 並列時は 1 リクエストが 4 倍以上遅くなり、タイムアウトが発生する。  
**禁止事項**: CONCURRENCY を 2 以上にする変更

---

### INV-05: タイムアウト設定

| 箇所 | 値 | ファイル |
|---|---|---|
| UI → Next.js API Route | 180,000ms | `src/app/predict-batch/page.tsx` |
| Next.js → FastAPI | 300,000ms | `src/app/api/analyze-race/route.ts` |
| FastAPI maxDuration | 300s | `src/app/api/analyze-race/route.ts` |
| FastAPI 内部（オッズ再スクレイプ） | 60s | `routers/predict.py` |

**禁止事項**: 上記より短い値への変更

---

### INV-06: 未来情報ブラックリスト

```
While モデル推論を行う,
the system shall POST_RACE_FIELDS に含まれるカラムを必ず推論入力から除外する。
```

`POST_RACE_FIELDS` は `routers/predict.py` に定義。含む主なカラム: `win`, `place`, `finish_order`, `finish_time`, `return_tables` など。

---

### INV-07: スクレイピングインターバル

```
While スクレイピングループを実行する,
when 各ページリクエストの間,
the system shall 最低 1.0 秒のスリープを挿入する。
```

| 箇所 | 最低値 | ファイル |
|---|---|---|
| 日付ループ（過去データ） | 1.0s | `scraping/jobs.py` `_pre_sleep` |
| レース間インターバル | 1.0s | `scraping/jobs.py` `_inter_race_sleep` |
| 馬詳細 4 頭ごと | 1.0s | `scraping/race.py` |
| 血統ページ補完 | 1.0s | `scraping/horse.py` |

---

### INV-08: 認証ミドルウェア

```
While FastAPI がリクエストを受信する,
the system shall Supabase JWT ミドルウェアを全エンドポイントに適用する。
the system shall 以下のパスのみ認証を免除する:
  {"/"、"/health"、"/docs"、"/openapi.json"、"/redoc"}
```

**禁止事項**: exempt_paths の無断拡張、認証ミドルウェアの削除

---

## 4. ポート規約（Port Conventions）

| サービス | ポート | 変更可否 |
|---|---|---|
| Next.js | 3000 | 変更不可 |
| FastAPI | 8000 | 変更不可 |
| FastAPI デバッグ（debugpy） | 5678 | 変更不可 |

---

## 5. データベース規約（DB Conventions）

| テーブル | 役割 | 格納形式 |
|---|---|---|
| `races_ultimate` | レース基本情報 | JSON（`data` カラム） |
| `race_results_ultimate` | 馬ごとの結果 | JSON（`data` カラム） |
| `scraped_dates` | 取得済み日付（省略用） | TEXT |
| `pedigree_cache` | 血統キャッシュ | TEXT |

**禁止事項**: テーブルスキーマの変更（`data` カラムの JSON 構造を直接扱うコードへの影響が大きい）

---

## 6. モデル規約（Model Conventions）

- 保存形式: `model_win_lightgbm_{start}_{end}_ultimate.joblib`
- 最新モデルは `models/` ディレクトリ内のファイル名の降順で決定
- AUC 目標: 0.85 以上
- 特徴量数: 87 カラム固定（ultimate モード）

---

## 7. 関連 Spec ファイル

| ファイル | 対象機能 |
|---|---|
| `docs/specs/SYSTEM.md` | 本ファイル（全体概要・不変条件） |
| `docs/specs/scraping.md` | データ取得・スクレイピング |
| `docs/specs/predict.md` | 予測・analyze_race エンドポイント |
| `docs/specs/ui.md` | UI / predict-batch ページ |
