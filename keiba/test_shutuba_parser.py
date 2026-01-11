"""出馬表パーサーのテスト"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import requests
from keiba_ai.netkeiba.parsers import parse_shutuba_table

# テスト用レースID
race_id = "202401010101"
url = f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"

print(f"出馬表取得: {race_id}")
print("=" * 60)

session = requests.Session()
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}
response = session.get(url, headers=headers, timeout=30)
response.encoding = response.apparent_encoding or 'EUC-JP'

df = parse_shutuba_table(response.text)

print(f"行数: {len(df)}")
print(f"カラム数: {len(df.columns)}")
print()
print("カラム名:")
for i, col in enumerate(df.columns, 1):
    print(f"  {i:2d}. {col}")

print()
print("重要カラムの存在確認:")
important_cols = ['race_id', 'umaban', 'horse_no', 'horse_name', 'horse_id', 'jockey_id']
for col in important_cols:
    exists = col in df.columns
    print(f"  {col}: {'✓' if exists else '✗'}")

print()
print("サンプルデータ（最初の3行）:")
display_cols = [c for c in ['horse_no', 'horse_name', 'horse_id', 'jockey_name'] if c in df.columns]
if display_cols:
    print(df[display_cols].head(3).to_string(index=False))
