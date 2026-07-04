"""
現在DB内のレースデータに対して、学習特徴量が計算できるか確認するスクリプト。
"""
import sys
import warnings
import sqlite3
import pathlib
import joblib
import pandas as pd
import numpy as np

warnings.filterwarnings('ignore')
sys.path.insert(0, 'keiba')

# ── 1. DB 状況確認 ──────────────────────────────────────────
conn = sqlite3.connect('keiba/data/keiba_ultimate.db')
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM races_ultimate")
race_count = cur.fetchone()[0]
cur.execute("SELECT race_id FROM races_ultimate ORDER BY race_id DESC LIMIT 3")
latest_races = [r[0] for r in cur.fetchall()]
cur.execute("SELECT COUNT(*) FROM race_results_ultimate")
result_count = cur.fetchone()[0]
conn.close()

print("=== DB 状態 ===")
print(f"races_ultimate:        {race_count:,} レース")
print(f"race_results_ultimate: {result_count:,} 行")
print(f"最新 race_id: {latest_races}")

# ── 2. 特徴量生成 ──────────────────────────────────────────
print("\n=== 特徴量生成 ===")
from keiba_ai.db_ultimate_loader import load_ultimate_training_frame
from keiba_ai.feature_engineering import add_derived_features
from keiba_ai.constants import UNNECESSARY_COLUMNS, FUTURE_FIELDS

DB_PATH = pathlib.Path('keiba/data/keiba_ultimate.db')
df_raw = load_ultimate_training_frame(DB_PATH)
print(f"ロード:  {len(df_raw):,} 行 x {df_raw.shape[1]} 列")

df_feat = add_derived_features(df_raw)
print(f"特徴量生成後: {df_feat.shape[1]} 列")

drop_post = [c for c in FUTURE_FIELDS if c in df_feat.columns]
df_train = df_feat.drop(columns=drop_post, errors='ignore')
drop_unnec = [c for c in UNNECESSARY_COLUMNS if c in df_train.columns]
df_train = df_train.drop(columns=drop_unnec, errors='ignore')
available = set(df_train.columns)
print(f"学習用列 (FUTURE除外{len(drop_post)}/UNNECESSARY除外{len(drop_unnec)}後): {len(available)} 列")
valid_rows = int(df_train['finish_position'].notna().sum()) if 'finish_position' in df_train.columns else 0
print(f"学習行数 (finish_position有): {valid_rows:,} 行")

# ── 3. モデル別カバレッジ確認 ──────────────────────────────
print("\n=== モデル別 特徴量カバレッジ ===")
models_dir = pathlib.Path('python-api/models')

for pattern, label in [
    ('model_speed_deviation_*.joblib', 'speed_deviation'),
    ('model_win_*.joblib', 'win'),
    ('model_place3_*.joblib', 'place3'),
]:
    files = sorted(models_dir.glob(pattern), key=lambda p: p.stem)
    if not files:
        print(f"\n[{label}] モデルなし")
        continue
    latest_f = files[-1]
    try:
        bundle = joblib.load(latest_f)
    except Exception as e:
        print(f"\n[{label}] ロード失敗: {e}")
        continue
    if not isinstance(bundle, dict):
        print(f"\n[{label}] bundle形式不明")
        continue
    feat_cols = bundle.get('feature_columns', [])
    if not feat_cols:
        print(f"\n[{label}] feature_columns なし")
        continue

    ok_cols      = [c for c in feat_cols if c in available]
    missing_cols = [c for c in feat_cols if c not in available]
    miss_rate    = len(missing_cols) / max(len(feat_cols), 1)

    high_nan = [(c, float(df_train[c].isna().mean())) for c in ok_cols if df_train[c].isna().mean() > 0.3]
    high_nan.sort(key=lambda x: -x[1])

    if miss_rate == 0:
        status = "OK 学習可"
    elif miss_rate <= 0.10:
        status = "NaN補完で可"
    else:
        status = "再学習必要（モデルと定数が不整合）"

    print(f"\n[{label}] {latest_f.name}")
    print(f"  特徴量数: {len(feat_cols)} | OK: {len(ok_cols)} | 欠損: {len(missing_cols)} ({miss_rate:.0%}) -> {status}")
    if missing_cols:
        print(f"  欠損リスト: {missing_cols}")
    if high_nan:
        print(f"  NaN>30%:   {[(k, f'{v:.0%}') for k,v in high_nan[:8]]}")

# ── 5. 特定列の存在確認 ──────────────────────────────────────
print("\n=== 特定列の存在確認 ===")
check_cols = [
    'venue_encoded', 'venue_code_encoded', 'race_class_encoded', 'sex_encoded',
    'field_condition_encoded', 'rest_category', 'jockey_course_win_rate',
    'sire_win_rate', 'gate_win_rate', 'jt_combo_win_rate_smooth',
    'finish_position',  # target (FUTURE_FIELDSに含まれるはず)
]
for c in check_cols:
    in_train = c in available
    in_feat  = c in df_feat.columns
    in_raw   = c in df_raw.columns
    nan_rate = f"{float(df_feat[c].isna().mean()):.0%}" if in_feat else "N/A"
    print(f"  {c:35s} raw={str(in_raw):<5} feat={str(in_feat):<5} train={str(in_train):<5} nan={nan_rate}")

print("\n  df_train 全列:")
print(" ", sorted(available))
