"""
1レース予測のステップ別処理時間を計測するプロファイリングスクリプト
Usage: python data/profile_predict.py [race_id]
"""
import sys
import time
import json
import sqlite3
import contextlib

sys.path.insert(0, 'keiba')
sys.path.insert(0, 'python-api')

RACE_ID = sys.argv[1] if len(sys.argv) > 1 else '202605021201'

@contextlib.contextmanager
def timer(label):
    t0 = time.perf_counter()
    yield
    elapsed = time.perf_counter() - t0
    print(f"  [{elapsed:6.2f}s] {label}")

print(f"=== 予測プロファイリング: {RACE_ID} ===\n")

# Step 1: DB ロード
with timer("app_config import (モデルロード含む)"):
    from app_config import ULTIMATE_DB, get_latest_model, load_model_bundle  # type: ignore

with timer("全履歴 DB ロード (_load_hist_cached 相当)"):
    from keiba_ai.db_ultimate_loader import load_ultimate_training_frame as _ltf  # type: ignore
    hist_df = _ltf(ULTIMATE_DB)
    print(f"         → {len(hist_df):,} 行 / {len(hist_df.columns)} 列")

with timer("training_data ロード"):
    import pandas as pd
    conn = sqlite3.connect(str(ULTIMATE_DB))
    training_df = pd.read_sql("SELECT * FROM training_data", conn)
    conn.close()
    print(f"         → {len(training_df):,} 行")

with timer("speed_figures ロード"):
    conn = sqlite3.connect(str(ULTIMATE_DB))
    sf_df = pd.read_sql("SELECT * FROM speed_figures", conn)
    conn.close()
    print(f"         → {len(sf_df):,} 行")

# Step 2: レースデータ取得
with timer("レースデータ取得 (SQLite)"):
    conn = sqlite3.connect(str(ULTIMATE_DB))
    cur = conn.cursor()
    cur.execute("SELECT data FROM races_ultimate WHERE race_id = ?", (RACE_ID,))
    rrow = cur.fetchone()
    cur.execute(
        "SELECT data FROM race_results_ultimate WHERE race_id = ? ORDER BY json_extract(data, '$.horse_number')",
        (RACE_ID,)
    )
    hrows = cur.fetchall()
    conn.close()
    print(f"         → {len(hrows)} 頭")

if not rrow or not hrows:
    print(f"ERROR: {RACE_ID} がDB未登録")
    sys.exit(1)

race_data = json.loads(rrow[0])
horse_records = []
for hr in hrows:
    hd = json.loads(hr[0])
    hd['race_id'] = RACE_ID
    for k, v in race_data.items():
        if k not in hd or hd[k] is None:
            hd[k] = v
    horse_records.append(hd)
df_pred = pd.DataFrame(horse_records)

# Step 3: モデルロード
with timer("モデルロード (load_model_bundle)"):
    model_path = get_latest_model()
    bundle = load_model_bundle(model_path)
    print(f"         → {model_path.name if model_path else 'None'}")

# Step 4: full_hist 構築 (エンティティフィルタ適用)
with timer("full_hist エンティティフィルタ + concat"):
    if 'race_id' in hist_df.columns:
        hist_df = hist_df[hist_df['race_id'] != RACE_ID]

    # エンティティフィルタ
    import functools
    conds = []
    for col in ('horse_id', 'jockey_id', 'trainer_id'):
        if col in hist_df.columns and col in df_pred.columns:
            ids = set(df_pred[col].dropna())
            if ids:
                conds.append(hist_df[col].isin(ids))
    for col in ('sire', 'damsire'):
        if col in hist_df.columns and col in df_pred.columns:
            ids = set(df_pred[col].dropna())
            if ids:
                conds.append(hist_df[col].isin(ids))
    venue = df_pred['venue'].iloc[0] if 'venue' in df_pred.columns else None
    if venue and 'venue' in hist_df.columns:
        conds.append(hist_df['venue'] == venue)
    if conds:
        mask = functools.reduce(lambda a, b: a | b, conds)
        hist_df_filtered = hist_df[mask]
    else:
        hist_df_filtered = hist_df
    print(f"         → フィルタ前: {len(hist_df):,}行 → フィルタ後: {len(hist_df_filtered):,}行 ({len(hist_df_filtered)/len(hist_df)*100:.0f}%)")
    full_hist = pd.concat([hist_df_filtered, df_pred], ignore_index=True)
    print(f"         → concat後: {len(full_hist):,} 行")

# Step 5: add_derived_features のステップ別計測
import pandas as pd
from keiba_ai.constants import FUTURE_FIELDS  # type: ignore

# POST_RACE_FIELDS 除外
drop_future = [c for c in FUTURE_FIELDS if c in df_pred.columns]
df_work = df_pred.drop(columns=drop_future)

print("\n--- add_derived_features ステップ別 ---")
from keiba_ai import feature_engineering as fe  # type: ignore

with timer("_fe_days_from_history"):
    df_work = fe._fe_days_from_history(df_work, full_hist)
with timer("_fe_horse_category"):
    df_work = fe._fe_horse_category(df_work)
with timer("_fe_id_season"):
    df_work = fe._fe_id_season(df_work)
with timer("_fe_course"):
    df_work = fe._fe_course(df_work)
with timer("_fe_market"):
    df_work = fe._fe_market(df_work)
with timer("_fe_prev_race"):
    df_work = fe._fe_prev_race(df_work)
with timer("_fe_opponent"):
    df_work = fe._fe_opponent(df_work)
with timer("_fe_holding_time"):
    df_work = fe._fe_holding_time(df_work)
with timer("_fe_lap"):
    df_work = fe._fe_lap(df_work)
with timer("_fe_payout"):
    df_work = fe._fe_payout(df_work)
with timer("_fe_corner_position"):
    df_work = fe._fe_corner_position(df_work)
with timer("_fe_speed_figures"):
    df_work = fe._fe_speed_figures(df_work, sf_df)
with timer("_fe_training"):
    df_work = fe._fe_training(df_work, training_df)

# _fe_history サブ関数別
print("\n--- _fe_history サブ関数別 ---")
h = full_hist.copy()

# running_style_num / race_class_num 前処理
if 'running_style_num' not in h.columns and 'corner_positions_list' in h.columns:
    from keiba_ai.feature_engineering import classify_running_style, _RUNNING_STYLE_NUM  # type: ignore
    nh_h = h.get('n_horses', h.groupby('race_id', sort=False)['race_id'].transform('count'))
    h['running_style'] = [classify_running_style(c, n) for c, n in zip(h['corner_positions_list'], nh_h)]
    h['running_style_num'] = h['running_style'].map(_RUNNING_STYLE_NUM)

with timer("_feh_jockey_course"):
    df_work, h = fe._feh_jockey_course(df_work, h)
with timer("_feh_horse_aptitude"):
    df_work, h = fe._feh_horse_aptitude(df_work, h)
with timer("_feh_gate_bias"):
    df_work, h = fe._feh_gate_bias(df_work, h)
with timer("_feh_jt_combo"):
    df_work, h = fe._feh_jt_combo(df_work, h)
with timer("_feh_entity_career"):
    df_work, h = fe._feh_entity_career(df_work, h)
with timer("_feh_recent_form"):
    df_work, h = fe._feh_recent_form(df_work, h)
with timer("_feh_entity_recent30"):
    df_work, h = fe._feh_entity_recent30(df_work, h)
with timer("_feh_last_3f"):
    df_work, h = fe._feh_last_3f(df_work, h)
with timer("_feh_payout_history"):
    df_work, h = fe._feh_payout_history(df_work, h)
with timer("_feh_running_style"):
    df_work, h = fe._feh_running_style(df_work, h)
with timer("_feh_field_strength"):
    df_work, h = fe._feh_field_strength(df_work, h)
with timer("_feh_race_dynamics"):
    df_work, h = fe._feh_race_dynamics(df_work, h)
with timer("_feh_jockey_running_style"):
    df_work, h = fe._feh_jockey_running_style(df_work, h)
with timer("_feh_horse_speed"):
    df_work, h = fe._feh_horse_speed(df_work, h)
with timer("_fe_missing_flags"):
    df_work = fe._fe_missing_flags(df_work)

# Step 6: optimizer.transform + predict
print("\n--- モデル推論 ---")
from routers.predict import ModelPredictor, _drop_non_features  # type: ignore
predictor = ModelPredictor(bundle, model_path)

with timer("optimizer.transform"):
    import numpy as np
    df_opt = predictor.optimizer.transform(df_work) if predictor.optimizer else df_work

with timer("_drop_non_features + feature_columns align"):
    from app_config import verify_feature_columns  # type: ignore
    X = _drop_non_features(df_opt)
    X = verify_feature_columns(X, bundle)

with timer("model.predict (LightGBM)"):
    raw_scores = predictor.model.predict(X)
    print(f"         → {len(raw_scores)} 頭分スコア")

print("\n=== 完了 ===")
