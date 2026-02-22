"""
ローカルパイプライン検証スクリプト
supabase_export.json → SQLite → 特徴量エンジニアリング → LightGBM学習

Usage:
    python run_local_pipeline.py
"""

import sys, json, sqlite3, os
from pathlib import Path

# keiba_ai モジュールパスを追加
sys.path.insert(0, str(Path(__file__).parent / "keiba"))

import pandas as pd
import numpy as np

# ── 1. JSON → SQLite 変換 ──────────────────────────────────────────────────
EXPORT_JSON = Path(__file__).parent / "keiba" / "data" / "supabase_export.json"
DB_PATH = Path(__file__).parent / "keiba" / "data" / "keiba_local_validate.db"

print("=" * 70)
print("Step 1: JSON → SQLite 変換")
print("=" * 70)

with open(EXPORT_JSON, encoding="utf-8-sig") as f:
    export_data = json.load(f)

races_raw = export_data["races"]
results_raw = export_data["results"]
print(f"  races: {len(races_raw)}")
print(f"  results: {len(results_raw)}")

conn = sqlite3.connect(str(DB_PATH))
cur = conn.cursor()
cur.execute("DROP TABLE IF EXISTS races_ultimate")
cur.execute("DROP TABLE IF EXISTS race_results_ultimate")
cur.execute("CREATE TABLE races_ultimate (race_id TEXT PRIMARY KEY, data TEXT NOT NULL)")
cur.execute("CREATE TABLE race_results_ultimate (race_id TEXT, data TEXT NOT NULL)")
for r in races_raw:
    data_str = r["data"] if isinstance(r["data"], str) else json.dumps(r["data"], ensure_ascii=False)
    cur.execute("INSERT OR REPLACE INTO races_ultimate VALUES (?,?)", (r["race_id"], data_str))
for r2 in results_raw:
    data_str = r2["data"] if isinstance(r2["data"], str) else json.dumps(r2["data"], ensure_ascii=False)
    cur.execute("INSERT INTO race_results_ultimate VALUES (?,?)", (r2["race_id"], data_str))
conn.commit()
conn.close()
print(f"  → SQLite保存: {DB_PATH}")

# ── 2. データロード ──────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("Step 2: データロード (db_ultimate_loader)")
print("=" * 70)

from keiba_ai.db_ultimate_loader import load_ultimate_training_frame
df_raw = load_ultimate_training_frame(str(DB_PATH))
print(f"  ロード行数: {len(df_raw)}")
print(f"  カラム数: {len(df_raw.columns)}")
print(f"  カラムリスト: {sorted(df_raw.columns.tolist())}")

# ── 3. 特徴量エンジニアリング ───────────────────────────────────────────────
print("\n" + "=" * 70)
print("Step 3: 特徴量エンジニアリング (add_derived_features)")
print("=" * 70)

from keiba_ai.feature_engineering import add_derived_features
df_feat = add_derived_features(df_raw.copy(), full_history_df=df_raw.copy())
print(f"  特徴量エンジニアリング後 行数: {len(df_feat)}")
print(f"  特徴量エンジニアリング後 カラム数: {len(df_feat.columns)}")

# ── 4. LightGBM特徴量最適化 ─────────────────────────────────────────────────
print("\n" + "=" * 70)
print("Step 4: LightGBM特徴量最適化 (prepare_for_lightgbm_ultimate)")
print("=" * 70)

from keiba_ai.lightgbm_feature_optimizer import prepare_for_lightgbm_ultimate

# win/place フラグを作成
if "finish" in df_feat.columns or "finish_position" in df_feat.columns:
    fin_col = "finish" if "finish" in df_feat.columns else "finish_position"
    fin_num = pd.to_numeric(df_feat[fin_col], errors="coerce")
    df_feat["win"] = (fin_num == 1).astype(int)
    df_feat["place"] = (fin_num <= 3).astype(int)
    print(f"  win=1: {df_feat['win'].sum()}, place=1: {df_feat['place'].sum()}")

result = prepare_for_lightgbm_ultimate(df_feat.copy(), target_col="win")
if isinstance(result, tuple) and len(result) == 3:
    X, optimizer, cat_feats = result
    y = None
elif isinstance(result, tuple) and len(result) == 2:
    X, y = result
    cat_feats = None
    optimizer = None
else:
    X = result
    y = None
    cat_feats = None
    optimizer = None

print(f"\n  最適化後 行数: {len(X)}")
print(f"  最適化後 特徴量数: {len(X.columns)}")

# ── 5. 全特徴量リスト出力 ────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("Step 5: 学習に使用する全特徴量リスト")
print("=" * 70)

all_features = X.columns.tolist()
print(f"\n【特徴量総数: {len(all_features)}個】\n")

# カテゴリ別に分類して表示
cat_enc = [c for c in all_features if c.endswith("_encoded")]
numeric_main = [c for c in all_features if not c.endswith("_encoded") and not c.startswith("sex_")
                and not c.startswith("pace_") and not c.startswith("rest_") 
                and not c.startswith("pop_trend_") and not c.startswith("lap_")
                and c not in ("race_id", "horse_id", "jockey_id", "trainer_id", "owner_id")]
id_cols = [c for c in all_features if c in ("race_id", "horse_id", "jockey_id", "trainer_id", "owner_id")]
dummy_cols = [c for c in all_features if c.startswith(("sex_", "pace_", "rest_", "pop_trend_"))]
lap_cols = [c for c in all_features if c.startswith("lap_")]

print(f"【カテゴリカル変数（エンコード済み）: {len(cat_enc)}個】")
for c in cat_enc:
    print(f"  {c}")

print(f"\n【数値変数: {len(numeric_main)}個】")
for c in sorted(numeric_main):
    print(f"  {c}")

if dummy_cols:
    print(f"\n【ダミー変数: {len(dummy_cols)}個】")
    for c in dummy_cols:
        print(f"  {c}")

if lap_cols:
    print(f"\n【ラップタイム変数: {len(lap_cols)}個】")
    for c in lap_cols:
        print(f"  {c}")

if id_cols:
    print(f"\n【ID変数（学習時除外推奨）: {len(id_cols)}個】")
    for c in id_cols:
        print(f"  {c}")

print(f"\n【全特徴量（完全リスト）】")
for i, c in enumerate(all_features, 1):
    print(f"  {i:3d}. {c}")

# ── 6. LightGBM学習テスト ────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("Step 6: LightGBM学習テスト")
print("=" * 70)

# IDカラムを除外
exclude_cols = [c for c in X.columns if c in ("race_id", "horse_id", "jockey_id", "trainer_id", "owner_id")]
X_train = X.drop(columns=exclude_cols, errors="ignore")

if y is None and "win" in df_feat.columns:
    y = df_feat["win"].values

if y is not None and len(X_train) > 0:
    try:
        import lightgbm as lgb
        from sklearn.model_selection import cross_val_score

        print(f"  学習データ: {X_train.shape}")
        print(f"  正例数: {y.sum()}")

        cat_feats_available = [c for c in (cat_feats or []) if c in X_train.columns]
        dtrain = lgb.Dataset(X_train, label=y, categorical_feature=cat_feats_available or "auto")
        params = {
            "objective": "binary",
            "metric": "auc",
            "num_leaves": 31,
            "learning_rate": 0.05,
            "n_estimators": 100,
            "verbose": -1,
        }
        cv_result = lgb.cv(params, dtrain, nfold=3, num_boost_round=100,
                           callbacks=[lgb.early_stopping(10), lgb.log_evaluation(-1)])
        best_auc = max(cv_result["valid auc-mean"])
        print(f"\n  ✅ LightGBM 3-fold CV AUC: {best_auc:.4f}")
        print(f"  ✅ パイプライン全体が正常に動作しました！")
    except Exception as e:
        print(f"  ⚠️ LightGBM学習エラー: {e}")
        import traceback
        traceback.print_exc()
else:
    print("  ⚠️ 正解ラベルまたは学習データなし")

print("\n" + "=" * 70)
print("検証完了")
print("=" * 70)
