---
name: predict-scrape-feature-adaptation
description: '予測スクレイプエラー対応 & 特徴量適応スキル。Use when: フロントエンドの予測時にスクレイプエラーが出る / IPブロック以外の原因でanalyze_raceが失敗する / 学習特徴量やスクレイプフィールドを変更したあと予測が壊れる / optimizer.transform が失敗する / asyncio.TimeoutError / NameError が出る / 特徴量セットを変えたあとモデルと推論パイプラインをどう同期するか分からない。Keywords: スクレイプエラー, scrape error, analyze_race, predict error, 特徴量適応, feature adaptation, optimizer.transform, SCRAPE_HEADERS, get_random_headers, verify_feature_columns, NameError asyncio'
---

# 予測スクレイプエラー対応 & 特徴量適応スキル

`/api/analyze_race` 予測時のスクレイプエラーと、学習特徴量変更後の推論パイプライン自動適応に関する知識と対処手順。

---

## 1. エラー分類と根本原因

| エラー種別 | 症状 | ファイル | 根本原因 |
|-----------|------|---------|---------|
| **IPブロック** | Cloudflare 38バイト HTML / 429 | `scraping/race.py` | netkeiba のボット検知（対処不可） |
| **asyncio NameError** | `NameError: name 'asyncio' is not defined` | `routers/predict.py` | `import asyncio` 欠落 → `asyncio.TimeoutError` catch 失敗 |
| **固定User-Agent** | IPブロック頻度が上がる | `routers/predict.py` | `SCRAPE_HEADERS`（固定UA）を使用 → `get_random_headers()` に変更が必要 |
| **optimizer.transform 失敗** | 500 Internal Server Error | `routers/predict.py` | 特徴量変更後に旧 optimizer が新特徴量セットを処理できない |
| **add_derived_features 失敗** | 500 Internal Server Error | `routers/predict.py` | feature_engineering.py 変更後、新入力形式と不一致 |
| **特徴量不整合 (A-6)** | `[A-6 ASSERT] 特徴量不一致が重大` RuntimeError | `app_config.py` | 学習時と推論時の特徴量セットが 10% 超不一致 |

---

## 2. predict.py の修正パターン

### 2-A: import asyncio の追加（必須）

```python
# python-api/routers/predict.py — ファイル先頭のインポート
import asyncio           # ← 追加必須（asyncio.TimeoutError catch のため）
import time as _time
```

### 2-B: ランダム User-Agent を使用 (`get_random_headers`)

`SCRAPE_HEADERS`（固定UA）を全て `get_random_headers()` に置き換える:

```python
# ❌ 古い書き方（固定UA → IPブロック頻度が上がる）
from scraping.constants import SCRAPE_HEADERS
async with aiohttp.ClientSession(headers=SCRAPE_HEADERS, ...) as sess:
    ...

# ✅ 正しい書き方（リクエストごとにランダムUA）
from scraping.constants import get_random_headers
async with aiohttp.ClientSession(headers=get_random_headers(), ...) as sess:
    ...
```

**対象箇所 (predict.py)**:
1. オンデマンドスクレイプ（レースが DB に未登録時）
2. オッズ再スクレイプ（結果ページ・出馬表ページへのフォールバック）

### 2-C: optimizer.transform のフォールバック（特徴量変更対応）

```python
bundle_optimizer = bundle.get("optimizer")
bundle_cat_features = bundle.get("categorical_features", [])
if bundle_optimizer:
    try:
        df_pred_opt = bundle_optimizer.transform(df_pred)
    except Exception as _opt_err:
        logger.warning(
            f"[analyze] optimizer.transform 失敗({type(_opt_err).__name__}): {_opt_err}"
            f" → prepare_for_lightgbm_ultimate へフォールバック"
        )
        from keiba_ai.lightgbm_feature_optimizer import prepare_for_lightgbm_ultimate
        df_pred_opt, _, bundle_cat_features = prepare_for_lightgbm_ultimate(
            df_pred, is_training=False, optimizer=None
        )
else:
    from keiba_ai.lightgbm_feature_optimizer import prepare_for_lightgbm_ultimate
    df_pred_opt, _, bundle_cat_features = prepare_for_lightgbm_ultimate(
        df_pred, is_training=False, optimizer=None
    )
```

**同パターンを predict エンドポイントにも適用すること** (`if use_optimizer and optimizer is not None:` ブロック内)。

### 2-D: add_derived_features のフォールバック（feature_engineering 変更対応）

```python
try:
    df_pred = add_derived_features(df_pred, full_history_df=full_hist)
except Exception as _afe:
    logger.warning(f"[analyze] add_derived_features 部分失敗: {_afe} → 基本特徴量のみで続行")
# 重複列除去は必ずここで実行（try/except の外）
df_pred = df_pred.loc[:, ~df_pred.columns.duplicated()]
```

---

## 3. 特徴量適応の仕組み（システム全体像）

```
学習時:
  constants.py (UNNECESSARY_COLUMNS)
      ↓ 除外
  prepare_for_lightgbm_ultimate (optimizer)  ← optimizer が "どの特徴量を使うか" を記憶
      ↓                                         bundle["feature_columns"] に保存
  model.train()
      ↓
  bundle = {model, optimizer, feature_columns, ...}  ← joblib 保存

推論時:
  scrape → DB → df_pred
      ↓
  add_derived_features()   ← 派生特徴量を計算
      ↓
  optimizer.transform()    ← 学習時と同じ前処理（失敗時は prepare_for_lightgbm_ultimate へ）
      ↓
  verify_feature_columns() ← bundle["feature_columns"] と照合
                              欠損列 → NaN 補完（LightGBM はNaN を適切に処理）
                              余剰列 → 無視
      ↓
  model.predict()
```

### 特徴量変更後の対応フロー

```
1. constants.py (UNNECESSARY_COLUMNS) を変更
   ↓
2. FastAPI を再起動（constants.py をリロードするため）
   ↓
3. /api/train または iterative_optimize.py で再学習
   → 新 bundle が model_*_ultimate.joblib に保存される
   → bundle["feature_columns"] / bundle["optimizer"] が更新される
   ↓
4. 予測時は新 bundle を使用
   → verify_feature_columns が自動適応（欠損列 NaN 補完）
```

> **重要**: 再学習後は FastAPI の再起動が必要（モデルキャッシュをリセットするため）。

---

## 4. verify_feature_columns / assert_feature_columns の動作

`python-api/app_config.py` に実装されている 2 つの関数:

| 関数 | 動作 | 閾値超過時の挙動 |
|------|------|---------------|
| `assert_feature_columns()` | 欠損率が閾値(10%)超なら RuntimeError | predict.py 側で `except RuntimeError` → `verify_feature_columns` に続行 |
| `verify_feature_columns()` | 欠損列を NaN 補完 + feature_columns 順にソート | 常に補完して返す（失敗しない） |

```python
# 呼び出しパターン（predict.py / analyze_race 内）
try:
    assert_feature_columns(X, bundle)      # 重大不一致を事前警告
except RuntimeError as _ae:
    logger.warning(f"[A-6 ASSERT warn] {_ae} → NaN補完で続行")
X = verify_feature_columns(X, bundle)      # 必ず呼ぶ（補完 + 並び替え）
proba = model.predict(X)
```

---

## 5. スクレイプエラーの診断手順

### ステップ 1: ログ確認

```powershell
# FastAPI ログの最後 50 行を確認
Get-Content python-api\optuna_debug.log -Encoding UTF8 | Select-Object -Last 50
```

**エラーメッセージ別対処**:

```
Cloudflare ブロック検知 → IPブロック（対処不可。プロキシ設定で回避可能）
asyncio.TimeoutError   → predict.py に import asyncio が欠落（修正済: 2-A参照）
optimizer.transform 失敗 → 特徴量変更後の再学習が必要（修正済: 2-C参照）
[A-6 ASSERT] 特徴量不一致が重大 → UNNECESSARY_COLUMNS 変更後の再学習が必要
```

### ステップ 2: 予測 API チェックコマンド

```powershell
# APIヘルス確認
$r = Invoke-RestMethod -Uri "http://localhost:8000/health"

# モデル確認（feature_columns 数を表示）
$env:PYTHONIOENCODING="utf-8"
python-api\.venv\Scripts\python.exe -c "
import sys; sys.path.insert(0,'keiba'); import joblib
from pathlib import Path
for p in sorted(Path('python-api/models').glob('model_*.joblib')):
    m = joblib.load(p)
    fc = len(m.get('feature_columns', []))
    print(f'{p.name}  features={fc}  target={m.get(\"target\")}  auc={m.get(\"metrics\",{}).get(\"auc\",0):.4f}')
"

# 特定レースで予測テスト
$body = '{"race_id":"202665032212","bankroll":10000,"risk_mode":"balanced"}'
Invoke-RestMethod -Uri "http://localhost:8000/api/analyze_race" -Method Post `
    -Body $body -ContentType "application/json" -TimeoutSec 120
```

### ステップ 3: 特徴量不整合チェック

```powershell
# 最新モデルの feature_columns と現在の constants.py の整合確認
$env:PYTHONIOENCODING="utf-8"
python-api\.venv\Scripts\python.exe -c "
import sys; sys.path.insert(0,'keiba'); import joblib
from pathlib import Path
from keiba_ai.constants import UNNECESSARY_COLUMNS

# 最新モデル
models = sorted(Path('python-api/models').glob('model_speed_deviation_*.joblib'))
if models:
    m = joblib.load(models[-1])
    fc = set(m.get('feature_columns', []))
    print(f'モデル特徴量数: {len(fc)}')
    print(f'UNNECESSARY_COLUMNS 数: {len(UNNECESSARY_COLUMNS)}')
"
```

---

## 6. スクレイプ定数の管理

`python-api/scraping/constants.py` の重要な定数:

| 変数/関数 | 用途 |
|---------|------|
| `SCRAPE_HEADERS` | 固定UA（後方互換用。**新規コードでは使わない**） |
| `get_random_headers()` | リクエストごとにランダムUA（**推奨**） |
| `SCRAPE_PROXY_URL` | 環境変数 `SCRAPE_PROXY_URL` からプロキシURL取得 |
| `is_cloudflare_block(content)` | レスポンスが Cloudflare ブロックか判定 |

```python
# ✅ 新スクレイプコードのテンプレート
import aiohttp
from scraping.constants import get_random_headers, SCRAPE_PROXY_URL

timeout = aiohttp.ClientTimeout(total=60)
async with aiohttp.ClientSession(headers=get_random_headers(), timeout=timeout) as sess:
    kwargs = {}
    if SCRAPE_PROXY_URL:
        kwargs["proxy"] = SCRAPE_PROXY_URL
    async with sess.get(url, **kwargs) as resp:
        ...
```

---

## 7. 修正完了チェックリスト

```
□ python-api/routers/predict.py に import asyncio が追加されている
□ predict.py の SCRAPE_HEADERS が get_random_headers() に置き換えられている（2箇所）
□ predict.py の optimizer.transform が try-except で囲まれている（predict / analyze_race 両方）
□ predict.py の add_derived_features が try-except で囲まれている（predict / analyze_race 両方）
□ 特徴量変更後に再学習 → FastAPI 再起動 → 予測テスト の順で検証済み
```

---

## 8. 関連スキル

| スキル | 用途 |
|-------|------|
| `feature-profiling-analysis` | UNNECESSARY_COLUMNS 変更の決定プロセス |
| `model-leakage-check` | FUTURE_FIELDS 除外の監査 |
| `feature-importance-report` | どの特徴量が重要かを可視化 |
