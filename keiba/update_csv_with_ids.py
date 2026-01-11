"""既存のCSVファイルにhorse_id等を追加"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import requests
from keiba_ai.netkeiba.parsers import parse_result_table

csv_dir = Path("data/netkeiba/results_by_race")
csv_files = list(csv_dir.glob("*.csv"))

print(f"📁 {len(csv_files)} CSVファイルを処理")
print("=" * 60)

session = requests.Session()
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

updated = 0
skipped = 0
failed = 0

for i, csv_file in enumerate(csv_files, 1):
    race_id = csv_file.stem
    
    # 進捗表示
    if i % 100 == 0 or i == len(csv_files):
        print(f"  進行中... {i}/{len(csv_files)} ({updated} 更新 / {skipped} スキップ / {failed} 失敗)")
    
    # 既存CSVを読み込み
    df_old = pd.read_csv(csv_file, encoding='utf-8-sig')
    
    # horse_idがすでに存在して有効なデータがあればスキップ
    if 'horse_id' in df_old.columns and df_old['horse_id'].notna().any():
        skipped += 1
        continue
    
    # HTMLから再取得してパース
    try:
        url = f"https://race.netkeiba.com/race/result.html?race_id={race_id}"
        response = session.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or 'EUC-JP'
        
        df_new = parse_result_table(response.text)
        df_new["race_id"] = race_id
        
        # CSVを上書き保存
        df_new.to_csv(csv_file, index=False, encoding='utf-8-sig')
        updated += 1
        
    except Exception as e:
        failed += 1
        if failed <= 5:  # 最初の5個だけエラー表示
            print(f"    ❌ {race_id}: {e}")

print()
print("=" * 60)
print("CSV更新完了")
print("=" * 60)
print(f"【結果】")
print(f"  ✅ 更新: {updated} ファイル")
print(f"  ⏭  スキップ: {skipped} ファイル（既にhorse_id有）")
print(f"  ❌ 失敗: {failed} ファイル")
