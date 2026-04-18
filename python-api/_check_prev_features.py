import sys
sys.path.insert(0, 'keiba')
from keiba_ai.db_ultimate_loader import load_ultimate_training_frame

df = load_ultimate_training_frame('keiba/data/keiba_ultimate.db')
print(f"Training rows: {len(df)}, cols: {len(df.columns)}")

targets = [
    'prev_race_finish', 'prev_race_distance', 'prev_race_time',
    'prev2_race_finish', 'prev2_race_distance', 'prev2_race_time',
    'horse_weight_change', 'running_style', 'distance',
    'sire', 'damsire',
]
for c in targets:
    if c in df.columns:
        nn = df[c].notna().mean()
        print(f"  {c}: {nn:.1%} non-null")
    else:
        print(f"  {c}: NOT in columns")
