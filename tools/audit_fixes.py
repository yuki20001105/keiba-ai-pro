"""Audit all P0-P3 fixes after retraining."""
import sys, os, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'python-api'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'keiba'))
os.chdir(os.path.join(os.path.dirname(__file__), '..', 'python-api'))

import pandas as pd
from app_config import ULTIMATE_DB, get_latest_model, load_model_bundle
from keiba_ai.db_ultimate_loader import load_ultimate_training_frame
from keiba_ai.feature_engineering import add_derived_features

print("=" * 60)
print("AUDIT: P0-P3 Fixes")
print("=" * 60)

# P0-2: num_horses check
print("\n[P0-2] num_horses fix...")
df = load_ultimate_training_frame(ULTIMATE_DB)
nan_count = df['num_horses'].isna().sum()
print(f"  num_horses NaN: {nan_count} / {len(df)}  {'PASS' if nan_count == 0 else 'FAIL'}")
print(f"  mean num_horses: {df['num_horses'].mean():.1f}")

# Feature engineering check
print("\n[P2/P3] New features check...")
df_fe = add_derived_features(df.copy(), full_history_df=df.copy())
checks = {
    'P2-7 prev_speed_index':       'prev_speed_index',
    'P2-7 prev_speed_zscore':      'prev_speed_zscore',
    'P2-8 horse_surface_win_rate': 'horse_surface_win_rate',
    'P2-8 horse_surface_races':    'horse_surface_races',
    'P2-8 horse_dist_band_win_rate':'horse_dist_band_win_rate',
    'P2-8 horse_dist_band_races':  'horse_dist_band_races',
    'P2-9 gate_win_rate':          'gate_win_rate',
    'P3-10 jt_combo_races':        'jt_combo_races',
    'P3-10 jt_combo_win_rate':     'jt_combo_win_rate',
    'P3-10 jt_combo_win_rate_smooth': 'jt_combo_win_rate_smooth',
}
for label, col in checks.items():
    if col in df_fe.columns:
        nn = df_fe[col].notna().sum()
        print(f"  {label}: PRESENT  non-null={nn}/{len(df_fe)}")
    else:
        print(f"  {label}: MISSING")

# P0-1: model check
print("\n[P0-1] Model feature audit...")
try:
    m = get_latest_model()
    bundle = load_model_bundle(m)
    feats = bundle.get('feature_columns', [])
    dead = [c for c in ['horse_total_runs','horse_total_wins','horse_total_prize_money',
                         'horse_win_rate','log_prize','log_total_runs','is_surface_change'] if c in feats]
    new_ok = [c for c in ['prev_speed_index','prev_speed_zscore',
                           'horse_surface_win_rate','horse_dist_band_win_rate',
                           'gate_win_rate','jt_combo_win_rate'] if c in feats]
    print(f"  Model: {m.name}")
    print(f"  Features: {len(feats)}")
    auc_val = bundle.get('metrics', {}).get('auc', 0)
    cv_val  = bundle.get('metrics', {}).get('cv_auc_mean', 0)
    print(f"  AUC={auc_val:.4f}  CV={cv_val:.4f}")
    print(f"  Dead cols removed: {'PASS (none in model)' if not dead else 'FAIL: ' + str(dead)}")
    print(f"  New features in model: {new_ok}")
except Exception as e:
    print(f"  ERROR: {e}")

print("\n" + "=" * 60)
print("AUDIT COMPLETE")
print("=" * 60)
