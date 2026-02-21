"""
スクレイピングで取得した生特徴量 と モデル入力特徴量（エンジニアリング後）を確認するスクリプト
"""
import sys, json, sqlite3
from pathlib import Path
import pandas as pd
import numpy as np

# validation/ から実行しても root から実行しても動作するよう __file__ ベースで解決
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "keiba"))
from keiba_ai.db_ultimate_loader import load_ultimate_training_frame
from keiba_ai.feature_engineering import add_derived_features
from keiba_ai.lightgbm_feature_optimizer import prepare_for_lightgbm_ultimate
from keiba_ai.ultimate_features import UltimateFeatureCalculator

DB_PATH = str(_ROOT / "keiba" / "data" / "keiba_ultimate.db")

# ─────────────────────────────────────────
# Part 1: スクレイピングで取得した生特徴量
# ─────────────────────────────────────────
print("=" * 72)
print("  Part 1: スクレイピング生特徴量（DBに保存されているJSON keys）")
print("=" * 72)

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# race_results_ultimate のサンプル（完全データを持つもの優先）
cur.execute("""
    SELECT race_id, data FROM race_results_ultimate
    WHERE json_extract(data, '$.corner_1') IS NOT NULL
    ORDER BY race_id DESC LIMIT 1
""")
row = cur.fetchone()
if not row:
    cur.execute("SELECT race_id, data FROM race_results_ultimate ORDER BY race_id DESC LIMIT 1")
    row = cur.fetchone()

race_id_sample, data_json = row
horse_data = json.loads(data_json)

# races_ultimate のサンプル
cur.execute("SELECT data FROM races_ultimate ORDER BY race_id DESC LIMIT 1")
race_row = cur.fetchone()
race_meta = json.loads(race_row[0]) if race_row else {}
conn.close()

# --- race_results_ultimate (馬ごと) の生フィールド ---
print(f"\n【race_results_ultimate】 サンプル race_id={race_id_sample}")
print(f"  フィールド数: {len(horse_data)}")

# カテゴリ別に分類
cat_basic     = ['race_id','finish_position','bracket_number','horse_number','horse_name','horse_id',
                 'horse_url','sex_age','sex','age']
cat_jockey    = ['jockey_name','jockey_id','jockey_url','jockey_weight']
cat_trainer   = ['trainer_name','trainer_id','trainer_url']
cat_result    = ['finish_time','margin','odds','popularity']
cat_physical  = ['weight_kg','weight_change','horse_weight','weight']
cat_track     = ['corner_positions','corner_1','corner_2','corner_3','corner_4',
                 'corner_positions_list','last_3f','last_3f_rank']
cat_pedigree  = ['sire','dam','damsire']
cat_history   = ['horse_total_runs','horse_total_wins','horse_total_prize_money',
                 'prev_race_date','prev_race_venue','prev_race_distance',
                 'prev_race_finish','prev_race_weight','prev_race_time',
                 'prev2_race_date','prev2_race_venue','prev2_race_distance',
                 'prev2_race_finish','prev2_race_weight']
cat_prize     = ['prize_money']

def show_category(title, keys):
    present = [(k, horse_data.get(k)) for k in keys if k in horse_data]
    missing = [k for k in keys if k not in horse_data]
    print(f"\n  ┌─ {title} ({len(present)}取得 / {len(keys)}定義) ─")
    for k, v in present:
        v_str = str(v)[:40] if v is not None else "None"
        status = "✅" if v is not None else "⬜"
        print(f"  │ {status} {k:<35} = {v_str}")
    if missing:
        print(f"  │ ⬜ 未取得: {', '.join(missing)}")
    print(f"  └{'─'*60}")

show_category("基本情報",        cat_basic)
show_category("騎手",            cat_jockey)
show_category("調教師",          cat_trainer)
show_category("着順・オッズ",    cat_result)
show_category("馬体重",          cat_physical)
show_category("走行ポジション",  cat_track)
show_category("血統",            cat_pedigree)
show_category("過去戦績",        cat_history)
show_category("賞金",            cat_prize)

# その他（上記カテゴリ外）
all_categorized = set(cat_basic + cat_jockey + cat_trainer + cat_result + cat_physical +
                      cat_track + cat_pedigree + cat_history + cat_prize)
others = [(k, v) for k, v in horse_data.items() if k not in all_categorized]
if others:
    print(f"\n  ┌─ その他フィールド ({len(others)}件) ─")
    for k, v in others:
        v_str = str(v)[:40] if v is not None else "None"
        print(f"  │ ✅ {k:<35} = {v_str}")
    print(f"  └{'─'*60}")

# races_ultimate フィールド
print(f"\n【races_ultimate】 レースメタ情報")
race_meta_keys = ['race_id','race_name','venue','date','post_time','race_class',
                  'kai','day','course_direction','distance','track_type',
                  'weather','field_condition','num_horses','lap_cumulative','lap_sectional']
for k in race_meta_keys:
    v = race_meta.get(k)
    v_str = str(v)[:60] if v is not None else "None"
    status = "✅" if v is not None else "⬜"
    print(f"  {status} {k:<30} = {v_str}")

# ─────────────────────────────────────────
# Part 2: モデル入力特徴量（エンジニアリング後）
# ─────────────────────────────────────────
print()
print("=" * 72)
print("  Part 2: モデル入力特徴量（特徴量エンジニアリング後）")
print("=" * 72)

print("\nデータ読み込み中...")
df_raw = load_ultimate_training_frame(DB_PATH)
print(f"  読み込み: {df_raw.shape[0]}行 × {df_raw.shape[1]}列")

print("\nadd_derived_features 実行中...")
df_eng = add_derived_features(df_raw, full_history_df=df_raw)
print(f"  エンジニアリング後: {df_eng.shape[0]}行 × {df_eng.shape[1]}列")

print("\nUltimateFeatureCalculator 実行中...")
calc = UltimateFeatureCalculator(DB_PATH)
df_ult = calc.add_ultimate_features(df_eng)
print(f"  Ultimate特徴量後: {df_ult.shape[0]}行 × {df_ult.shape[1]}列")

print("\nprepare_for_lightgbm_ultimate 実行中...")
df_opt, feature_names, cat_features = prepare_for_lightgbm_ultimate(df_ult, is_training=True)

# 学習に使う特徴量だけ抜き出す
exclude_cols = ['win', 'place', 'race_id', 'horse_id', 'jockey_id', 'trainer_id',
                'finish_position', 'finish', 'owner_id']
X = df_opt.drop([c for c in exclude_cols if c in df_opt.columns], axis=1)
X = X.drop(columns=X.select_dtypes(include=['object']).columns.tolist(), errors='ignore')

print(f"\n  最終モデル入力: {X.shape[1]}列\n")

# 特徴量を出自別に分類
raw_cols        = []   # スクレイピング生データ由来
derived_cols    = []   # feature_engineering.py 由来
ultimate_cols   = []   # ultimate_features.py 由来
optimizer_cols  = []   # lightgbm_feature_optimizer.py 由来

RAW_ORIGIN = {
    'horse_number','bracket_number','jockey_weight','odds','popularity',
    'horse_weight','horse_weight_change','weight_change','age','distance',
    'num_horses','kai','day','corner_1','corner_2','corner_3','corner_4',
    'last_3f_time','last_3f_rank','time_seconds',
    'horse_total_runs','horse_total_wins','horse_total_prize_money',
    'prev_race_distance','prev_race_finish','prev_race_weight',
    'prev2_race_distance','prev2_race_finish',
}
DERIVED_ORIGIN = {
    'is_young','is_prime','is_veteran','corner_position_avg','corner_position_variance',
    'last_corner_position','position_change','last_3f_rank_normalized',
    'distance_change','distance_increased','distance_decreased',
    'days_since_last_race','horse_win_rate',
    'market_entropy','top3_probability',
    'jockey_course_win_rate','jockey_course_races',
    'horse_distance_win_rate','horse_distance_avg_finish',
    'trainer_recent_win_rate','jockey_place_rate_top2','jockey_show_rate',
    'trainer_place_rate_top2','trainer_show_rate',
    'venue_code','race_num','straight_length','inner_bias','inner_advantage',
    'track_type','corner_radius',
}
ULTIMATE_ORIGIN = {
    'past_10_races_count','past_10_avg_finish','past_10_std_finish',
    'past_10_best_finish','past_10_worst_finish','past_10_win_rate',
    'past_10_place_rate','past_10_show_rate','past_10_avg_popularity',
    'recent_3_avg_finish','past_7_avg_finish','finish_consistency','recent_form_score',
    'jockey_recent_win_rate','jockey_recent_place_rate','jockey_recent_show_rate',
    'jockey_recent_races','jockey_avg_finish',
    'trainer_recent_win_rate','trainer_recent_place_rate',
    'trainer_recent_show_rate','trainer_recent_races',
}

for col in X.columns:
    base = col.replace('_encoded','').replace('_win_rate','').replace('_avg_finish','').replace('_race_count','')
    if col in RAW_ORIGIN or base in RAW_ORIGIN:
        raw_cols.append(col)
    elif col in ULTIMATE_ORIGIN or base in ULTIMATE_ORIGIN:
        ultimate_cols.append(col)
    elif col in DERIVED_ORIGIN or base in DERIVED_ORIGIN:
        derived_cols.append(col)
    else:
        optimizer_cols.append(col)

def show_feature_group(title, cols, df=X):
    print(f"\n  ┌─ {title} ({len(cols)}列) ─")
    for c in sorted(cols):
        if c not in df.columns:
            print(f"  │  {c:<45} N/A")
            continue
        series = df[c]
        # 重複列の場合は最初の列を使う
        if isinstance(series, pd.DataFrame):
            series = series.iloc[:, 0]
        dtype = str(series.dtype)
        notna = series.notna().sum()
        total = len(df)
        fill  = notna / total * 100
        sample_val = series.dropna().iloc[0] if notna > 0 else None
        sample_str = f"{sample_val:.3g}" if isinstance(sample_val, (int, float, np.floating, np.integer)) else str(sample_val)[:15]
        fill_str = f"{fill:5.1f}%" 
        print(f"  │  {c:<45} {dtype:<10} fill={fill_str}  例={sample_str}")
    print(f"  └{'─'*70}")

show_feature_group("① スクレイピング生データ由来",   raw_cols)
show_feature_group("② 特徴量エンジニアリング由来",    derived_cols)
show_feature_group("③ Ultimate（馬・騎手・調教師履歴）由来", ultimate_cols)
show_feature_group("④ Optimizer（高カーディナリティ統計・エンコード）由来", optimizer_cols)

# 総括
print()
print("=" * 72)
print("  特徴量サマリー")
print("=" * 72)
print(f"  ① スクレイピング生データ:          {len(raw_cols):3d}列")
print(f"  ② 特徴量エンジニアリング:          {len(derived_cols):3d}列")
print(f"  ③ Ultimate履歴統計:                {len(ultimate_cols):3d}列")
print(f"  ④ Optimizer（統計化・エンコード）: {len(optimizer_cols):3d}列")
print(f"  ─────────────────────────────────────────")
print(f"  合計                               {X.shape[1]:3d}列")
print(f"\n  カテゴリカル特徴量: {len(cat_features)}列 → {cat_features}")

# 重複列を除去して欠損率チェック
X_dedup = X.loc[:, ~X.columns.duplicated()]
high_missing = []
for c in X_dedup.columns:
    fill_rate = X_dedup[c].notna().sum() / len(X_dedup)
    if fill_rate < 0.5:
        high_missing.append((c, (1 - fill_rate) * 100))
if high_missing:
    print(f"\n  ⚠️  欠損率50%超の特徴量 ({len(high_missing)}列):")
    for c, miss in sorted(high_missing, key=lambda x: -x[1])[:15]:
        print(f"     {c:<45} 欠損率={miss:.1f}%")

# 重複列の確認
dup_cols = X.columns[X.columns.duplicated()].tolist()
if dup_cols:
    print(f"\n  ⚠️  重複列あり: {dup_cols}  → 学習時に要除去")
