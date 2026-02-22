import sys, os
sys.path.insert(0, "c:/Users/yuki2/Documents/ws/keiba-ai-pro")
os.chdir("c:/Users/yuki2/Documents/ws/keiba-ai-pro")
from keiba.keiba_ai.db_ultimate_loader import load_ultimate_training_frame
from keiba.keiba_ai.feature_engineering import add_derived_features
from keiba.keiba_ai.lightgbm_feature_optimizer import LightGBMFeatureOptimizer
from pathlib import Path

df = load_ultimate_training_frame(Path("keiba/data/keiba_local_validate.db"))
print("Loaded:", df.shape)
df = add_derived_features(df)
print("After FE:", df.shape)
opt = LightGBMFeatureOptimizer()
X, cats = opt.fit_transform(df)
cols = set(X.columns)

print("=== LEAK CHECK ===")
leaks = ["corner_1","corner_2","corner_3","corner_4","last_3f_rank","last_3f_rank_normalized",
         "last_3f_time","time_seconds","position_change","last_corner_position",
         "corner_position_avg","corner_position_variance"]
for c in leaks:
    status = "LEAK" if c in cols else "OK removed"
    print("  " + c + ": " + status)

print("=== NEW FEATURES ===")
new_feats = ["is_surface_change","log_odds","log_prize","log_total_runs",
             "jockey_win_rate_smooth","jockey_has_history","rest_category","is_first_race"]
for c in new_feats:
    status = "OK added" if c in cols else "MISSING"
    print("  " + c + ": " + status)

print("Final feature count: " + str(len(cols)) + " columns")
print(sorted(cols))
