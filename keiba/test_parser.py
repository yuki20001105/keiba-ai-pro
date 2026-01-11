"""パーサー修正のテスト"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import requests
from keiba_ai.netkeiba.parsers import parse_result_table

# テスト用レースID（安田記念）
race_id = "202406050811"
url = f"https://race.netkeiba.com/race/result.html?race_id={race_id}"

print(f"テスト: {race_id}")
print("=" * 50)

session = requests.Session()
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}
response = session.get(url, headers=headers, timeout=30)
response.encoding = response.apparent_encoding or 'EUC-JP'
html = response.text

df = parse_result_table(html)
print(f"行数: {len(df)}")
print(f"カラム数: {len(df.columns)}")

# 実際のカラム名を表示（デバッグ用）
print(f"\n実際のカラム名（最初の10個）:")
for i, col in enumerate(df.columns[:10], 1):
    print(f"  {i}. '{col}' (repr: {repr(col)})")

# 重要なカラムの存在確認
key_cols = ['finish', 'horse_id', 'horse_name', 'jockey_id', 'trainer_id']
print(f"\n重要カラムの存在:")
for col in key_cols:
    exists = col in df.columns
    if exists and col == 'finish':
        non_null = df[col].notna().sum()
        print(f"  {col}: ✓ ({non_null}/{len(df)} = {non_null/len(df)*100:.0f}%)")
    elif exists:
        non_null = df[col].notna().sum()
        print(f"  {col}: ✓ ({non_null}/{len(df)})")
    else:
        print(f"  {col}: ✗ (不在)")

# horse_idの状態を確認
if 'horse_id' in df.columns:
    non_null = df['horse_id'].notna().sum()
    print(f"\nhorse_id詳細: {non_null}/{len(df)} 件（{non_null/len(df)*100:.1f}%）")
else:
    print("\nhorse_id カラムが見つかりません")

# サンプルデータ
print("\nサンプルデータ（最初の3頭）:")
cols_to_show = ['finish', 'horse_name', 'horse_id', 'jockey_id', 'trainer_id']
available_cols = [c for c in cols_to_show if c in df.columns]
if available_cols:
    sample = df[available_cols].head(3)
    for idx, row in sample.iterrows():
        print(f"  {idx+1}: finish={row.get('finish', 'N/A')}, horse={row.get('horse_name', 'N/A')}, horse_id={row.get('horse_id', 'N/A')}")
else:
    print("  表示可能なカラムがありません")
