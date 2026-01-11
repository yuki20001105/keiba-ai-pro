import pandas as pd
from pathlib import Path

csv_files = list(Path('data/netkeiba/results_by_race').glob('*.csv'))
print(f"Total CSV files: {len(csv_files)}")

# ランダムに10ファイルをサンプリング
samples = csv_files[:20]

for f in samples:
    df = pd.read_csv(f, encoding='utf-8-sig')
    has_horse_id = 'horse_id' in df.columns and not df['horse_id'].isna().all()
    print(f"{f.stem}: horse_id={has_horse_id}, columns={df.columns.tolist()[:5]}")
