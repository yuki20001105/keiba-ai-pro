# GitHub Copilot Instructions — keiba-ai-pro

このファイルは GitHub Copilot (AI) への永続的な指示です。  
コードを変更する前に必ず `docs/specs/SYSTEM.md` の不変条件（INV-01〜INV-08）を確認してください。

---

## プロジェクト概要

競馬 AI 予測システム。netkeiba.com からデータを収集し、LightGBM で勝率を予測し、Kelly 基準で購入推奨を出力する。

- **FastAPI** (python-api/, :8000) — ML バックエンド
- **Next.js** (src/, :3000) — UI + API プロキシ
- **SQLite** (keiba/data/keiba_ultimate.db) — 主データストア
- **Supabase** — 認証・購入記録のみ

---

## 絶対に変更してはならない事項（AI への最重要指示）

### 1. 予測パイプライン順序（INV-01）

```
DB読み込み → 特徴量生成 → POST_RACE_FIELDS除外 → LightGBM予測 → Kelly推奨
```
- 未来情報（着順・タイム等）の除外ステップを省略・移動しないこと

### 2. オッズ取得（INV-02）

- `_hr.get("odds") or _hr.get("win_odds")` の形式は**禁止**（0.0 を falsy 誤判定する）
- 必ず `is not None` で判定し、`df_pred` の逆引きマップを優先すること
- 全馬 odds=0 or NaN の場合のみ再スクレイプを実行すること

### 3. 日付判定（INV-03）

- `race_date < today` は**禁止**
- 当日含む過去レース: `race_date <= today` で結果ページを使用すること

### 4. 並列処理（INV-04）

- `CONCURRENCY = 1` を維持すること（`src/app/predict-batch/page.tsx`）
- FastAPI は単一 Python プロセス。GIL 競合でタイムアウトが発生する

### 5. タイムアウト値（INV-05）

- UI→API: 180,000ms 以上
- Next.js→FastAPI: 300,000ms 以上
- `export const maxDuration = 300` を削除しないこと

### 6. スクレイピングインターバル（INV-07）

- 各リクエスト間は最低 **1.0 秒** 必須
- `_pre_sleep`, `_inter_race_sleep` を 1.0 秒未満にしないこと

---

## コーディング規約

### Python (python-api/)

- `or` 演算子で数値を falsy 判定しない（`0.0`, `0` は falsy）
- 例外は `raise` せずサイレント続行する場面（Cloudflare ブロック等）に注意
- `datetime.now().strftime("%Y%m%d")` で今日の日付を生成すること

### TypeScript (src/)

- `fetch` に必ず `AbortSignal.timeout()` を設定すること
- `localStorage` への `ra-cache:` キャッシュ書き込みは try-catch で囲むこと

---

## 仕様書ファイル

変更前に必ず該当仕様書を確認すること:

| 対象機能 | 仕様書 |
|---|---|
| システム全体・不変条件 | `docs/specs/SYSTEM.md` |
| データ取得・スクレイピング | `docs/specs/scraping.md` |
| 予測エンドポイント（予定） | `docs/specs/predict.md` |
| UI / predict-batch（予定） | `docs/specs/ui.md` |

---

## スキルファイル

特定タスクが依頼されたら対応するスキルを必ず読み込むこと:

| タスク・キーワード | スキル |
|---|---|
| 特徴量重要度レポート / feature importance / モデル評価 / AUC レポート | `.github/skills/feature-importance-report/SKILL.md` |
| データリーク / leakage / FUTURE_FIELDS / 未来情報混入 | `.github/skills/model-leakage-check/SKILL.md` |
| プロファイリング解析 / 相関分析 / 欠損率 / 冗長特徴量 / 反復最適化 / ydata-profiling | `.github/skills/feature-profiling-analysis/SKILL.md` |
| 予測スクレイプエラー / analyze_race エラー / 特徴量変更後の予測適応 / optimizer.transform 失敗 / IPブロック以外の予測エラー / get_random_headers | `.github/skills/predict-scrape-feature-adaptation/SKILL.md` |
| git / コミット / ブランチ / リリース / タグ / ワークフロー / develop / main / release | `.github/skills/git-workflow/SKILL.md` |

### 反復最適化パイプラインの実行手順（10 イテレーション）

```
1. FastAPI サーバーを起動: python-api/main.py
2. 下記コマンドで 1 イテレーション実行:
   python-api\.venv\Scripts\python.exe python-api/training/optimizer.py \
       --start-iter 1 --iterations 1 --skip-scrape
3. docs/reports/iter_01_metrics.json の recommendations を確認
4. feature-profiling-analysis スキルの手順で constants.py 等を修正
5. --start-iter 2 --iterations 1 --skip-scrape で次のイテレーションを実行
6. ITR-10 まで（または収束まで）繰り返す

初回実行（全期間データ取得あり）:
   python-api\.venv\Scripts\python.exe python-api/training/optimizer.py \
       --start-iter 1 --iterations 1 --scrape-full
```

**特徴量変更後の検証**（必須）:
```bash
python-api\.venv\Scripts\python.exe -c "
import sys; sys.path.insert(0,'keiba')
from keiba_ai.constants import UNNECESSARY_COLUMNS
from keiba_ai.feature_engineering import add_derived_features
print('OK: constants loaded,', len(UNNECESSARY_COLUMNS), 'unnecessary cols')
"
```
