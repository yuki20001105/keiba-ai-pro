"""持ちタイムバックフィルのテスト"""
import sys
sys.path.insert(0, 'keiba')
from pathlib import Path
from keiba.keiba_ai.db_ultimate_loader import load_ultimate_training_frame

DB = Path('keiba/data/keiba_ultimate.db')
df = load_ultimate_training_frame(DB)

# holding_just_time_sec の取得状況
import pandas as pd
if 'holding_just_time_sec' in df.columns:
    total = len(df)
    has_hold = df['holding_just_time_sec'].notna().sum()
    pct = 100 * has_hold / total
    print(f"holding_just_time_sec 取得率: {has_hold:,}/{total:,} ({pct:.1f}%)")
    # 年別
    if 'race_date' in df.columns:
        df['year'] = df['race_date'].astype(str).str[:4]
        g = df.groupby('year')['holding_just_time_sec'].apply(lambda x: x.notna().sum())
        t = df.groupby('year').size()
        for y in sorted(g.index):
            print(f"  {y}: {g[y]:,}/{t[y]:,} ({100*g[y]/t[y]:.0f}%)")
else:
    print("holding_just_time_sec 列が存在しません")
