---
name: trainer
description: 'Trainer（トレーナー）スキル — モデル学習・特徴量エンジニアリング担当。Use when: LightGBMモデルの学習・再学習を行いたい / Optuna最適化の設定を変更したい / 特徴量の追加・削除・修正をしたい / AUC / 的中率が低い原因を調査したい / 過学習・データリークの疑いがある / ydata-profilingレポートを解析して特徴量を改善したい / 不要特徴量・高相関特徴量を整理したい / train / feature-lab ページの改修。Keywords: LightGBM, Optuna, 特徴量, feature engineering, AUC, 過学習, データリーク, leakage, プロファイリング, train, feature-lab, UNNECESSARY_COLUMNS, POST_RACE_FIELDS, add_derived_features, constants.py, optimizer'
---

# Trainer（トレーナー）— モデル学習・特徴量エンジニアリング

LightGBM モデルの学習から特徴量最適化まで、AIモデルの品質全般を担当。

---

## 担当ページ・ファイル

| 種別 | パス | 役割 |
|---|---|---|
| UI | `src/app/train/page.tsx` | 学習ジョブ開始・進捗・モデル管理 |
| UI | `src/app/feature-lab/page.tsx` | 特徴量重要度・カバレッジ分析 |
| API | `python-api/routers/train.py` | 学習ジョブエンドポイント |
| API | `python-api/routers/features.py` | 特徴量 API |
| Core | `python-api/training/optimizer.py` | Optuna最適化パイプライン |
| Core | `python-api/training/pipeline.py` | 学習パイプライン |
| Core | `keiba/keiba_ai/feature_engineering.py` | 特徴量生成（add_derived_features） |
| Core | `keiba/keiba_ai/constants.py` | UNNECESSARY_COLUMNS / POST_RACE_FIELDS 定義 |
| Catalog | `keiba/feature_catalog.yaml` | 特徴量カタログ（全特徴量の定義） |

---

## 学習パイプライン（INV-01 厳守）

```
SQLite DB 読み込み
    ↓
add_derived_features()  ← 特徴量エンジニアリング
    ↓
POST_RACE_FIELDS 除外   ← ★絶対に省略しない（データリーク防止）
    ↓
UNNECESSARY_COLUMNS 除外
    ↓
LightGBM + Optuna 最適化
    ↓
モデル保存（python-api/models/ または keiba/models/）
```

---

## 主要 API エンドポイント

| エンドポイント | 説明 |
|---|---|
| `POST /api/ml/train/start` | 学習ジョブ開始（非同期） |
| `GET /api/ml/train/status/{jobId}` | 学習進捗ポーリング |
| `GET /api/models?ultimate=true` | 学習済みモデル一覧 |
| `DELETE /api/models/{modelId}` | モデル削除 |
| `GET /api/features/summary` | 特徴量サマリー |
| `GET /api/features/importance` | 特徴量重要度（SHAP gain/split） |
| `GET /api/features/coverage` | カタログとの差分チェック |

---

## モデル ID 命名規則

```
{target}_{type}_{YYYYMMDD_HHMM}
例: speed_deviation_lightgbm_20260418_1928

target:
  - win        : 単勝（1着予測）
  - place3     : 複勝（3着以内予測）
  - speed_deviation : 速度偏差（推奨）
```

---

## 不変条件（必ず守ること）

### INV-01: POST_RACE_FIELDS の除外

```python
# keiba/keiba_ai/constants.py に定義
POST_RACE_FIELDS = [
    'finish', 'time', 'margin', 'last3f', 'pass_order',
    'actual_odds', 'actual_popularity', ...
]

# 特徴量生成後、学習前に必ず除外
features_to_use = [f for f in df.columns if f not in POST_RACE_FIELDS]
```

### データリーク防止チェック

```python
# 学習後に必ず確認
from keiba_ai.constants import UNNECESSARY_COLUMNS, POST_RACE_FIELDS
cols = set(df_train.columns)
leak_cols = cols & set(POST_RACE_FIELDS)
assert len(leak_cols) == 0, f"データリーク: {leak_cols}"
```

---

## 特徴量管理

### UNNECESSARY_COLUMNS（学習から除外する列）

```python
# keiba/keiba_ai/constants.py
UNNECESSARY_COLUMNS = [
    'race_id', 'horse_id', 'horse_name', ...  # ID・文字列など
]
```

### 特徴量追加の手順

1. `keiba/keiba_ai/feature_engineering.py` の `add_derived_features()` に追加
2. `keiba/feature_catalog.yaml` にカタログ登録
3. 検証コマンド実行:
   ```powershell
   python-api\.venv\Scripts\python.exe -c "
   import sys; sys.path.insert(0,'keiba')
   from keiba_ai.constants import UNNECESSARY_COLUMNS
   from keiba_ai.feature_engineering import add_derived_features
   print('OK: constants loaded,', len(UNNECESSARY_COLUMNS), 'unnecessary cols')
   "
   ```
4. 再学習 → AUC変化を確認

---

## 反復最適化パイプライン（10イテレーション）

```powershell
# 1イテレーション実行
python-api\.venv\Scripts\python.exe python-api/training/optimizer.py `
    --start-iter 1 --iterations 1 --skip-scrape

# レポート確認
# docs/reports/iter_01_metrics.json の recommendations を確認

# 次のイテレーション
python-api\.venv\Scripts\python.exe python-api/training/optimizer.py `
    --start-iter 2 --iterations 1 --skip-scrape
```

詳細は `feature-profiling-analysis` スキルを参照。

---

## 関連スキル（詳細タスク用）

| タスク | 参照スキル |
|---|---|
| 特徴量重要度レポート生成 | `feature-importance-report` |
| データリーク監査 | `model-leakage-check` |
| プロファイリング解析・反復最適化 | `feature-profiling-analysis` |

---

## よくあるトラブル

### AUC が低い（0.55以下）
```
1. feature-profiling-analysis スキルで高相関特徴量を除去
2. POST_RACE_FIELDS が除外されているか確認（逆に除外しすぎも悪化の原因）
3. 学習データ期間を調整（直近2年が効果的なことが多い）
```

### 学習ジョブがタイムアウト
```
原因: Optuna試行回数が多すぎる（100回推奨、200回以上は重い）
対処: n_trials を減らすか --skip-scrape オプションを使用
```
