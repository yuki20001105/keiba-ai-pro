"""強制的にCSVファイルを更新（テスト版：最初の10ファイル）"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import requests
import time
from keiba_ai.netkeiba.parsers import parse_result_table

csv_dir = Path("data/netkeiba/results_by_race")
csv_files = list(csv_dir.glob("*.csv"))[:10]  # テスト用に最初の10ファイルのみ

print(f"📁 {len(csv_files)} CSVファイルを強制更新")
print("=" * 60)

session = requests.Session()
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

updated = 0
failed = 0

for i, csv_file in enumerate(csv_files, 1):
    race_id = csv_file.stem
    print(f"\n[{i}/{len(csv_files)}] {race_id}")
    
    # HTMLから再取得してパース
    try:
        url = f"https://race.netkeiba.com/race/result.html?race_id={race_id}"
        response = session.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or 'EUC-JP'
        
        df_new = parse_result_table(response.text)
        df_new["race_id"] = race_id
        
        horse_id_count = df_new['horse_id'].notna().sum() if 'horse_id' in df_new.columns else 0
        finish_count = df_new['finish'].notna().sum() if 'finish' in df_new.columns else 0
        print(f"  データ: {len(df_new)}頭, horse_id={horse_id_count}, finish={finish_count}")
        
        # CSVを上書き保存
        df_new.to_csv(csv_file, index=False, encoding='utf-8-sig')
        updated += 1
        print(f"  → ✅ 更新完了")
        
        time.sleep(0.5)  # レート制限対策
        
    except Exception as e:
        failed += 1
        print(f"  → ❌ 失敗: {e}")

print()
print("=" * 60)
print("強制更新完了")
print("=" * 60)
print(f"【結果】")
print(f"  ✅ 更新: {updated} ファイル")
print(f"  ❌ 失敗: {failed} ファイル")
