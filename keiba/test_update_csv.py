"""既存のCSVファイルにhorse_id等を追加（テスト版：最初の10ファイル）"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import requests
import time
from keiba_ai.netkeiba.parsers import parse_result_table

csv_dir = Path("data/netkeiba/results_by_race")
csv_files = list(csv_dir.glob("*.csv"))[:10]  # テスト用に最初の10ファイルのみ

print(f"📁 {len(csv_files)} CSVファイルをテスト処理")
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
    print(f"\n[{i}/{len(csv_files)}] {race_id}")
    
    # 既存CSVを読み込み
    df_old = pd.read_csv(csv_file, encoding='utf-8-sig')
    has_horse_id = 'horse_id' in df_old.columns and df_old['horse_id'].notna().any()
    print(f"  既存: horse_id={'有' if has_horse_id else '無'}, {len(df_old)}頭")
    
    # horse_idがすでに存在して有効なデータがあればスキップ
    if has_horse_id:
        skipped += 1
        print(f"  → スキップ（既にhorse_id有）")
        continue
    
    # HTMLから再取得してパース
    try:
        url = f"https://race.netkeiba.com/race/result.html?race_id={race_id}"
        response = session.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or 'EUC-JP'
        
        df_new = parse_result_table(response.text)
        df_new["race_id"] = race_id
        
        new_horse_id_count = df_new['horse_id'].notna().sum() if 'horse_id' in df_new.columns else 0
        print(f"  新規: horse_id={new_horse_id_count}/{len(df_new)}頭")
        
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
print("テスト完了")
print("=" * 60)
print(f"【結果】")
print(f"  ✅ 更新: {updated} ファイル")
print(f"  ⏭  スキップ: {skipped} ファイル")
print(f"  ❌ 失敗: {failed} ファイル")
