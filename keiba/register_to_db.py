#!/usr/bin/env python3
"""CSVデータをデータベースに一括登録するスクリプト"""
import sqlite3
from pathlib import Path
import pandas as pd
import sys

def register_to_db():
    """CSVファイルをデータベースに登録"""
    print("=" * 80)
    print("CSVデータをデータベースに登録")
    print("=" * 80)
    
    db_path = Path("data/keiba.db")
    conn = sqlite3.connect(db_path)
    
    results_dir = Path("data/netkeiba/results_by_race")
    csv_files = sorted(results_dir.glob("*.csv"))
    
    if not csv_files:
        print("\n❌ CSVファイルが見つかりません")
        return 1
    
    print(f"\n📁 {len(csv_files)} CSVファイルを発見")
    
    # 既に登録済みのrace_idを確認
    cursor = conn.cursor()
    cursor.execute("SELECT race_id FROM races")
    existing_races = {row[0] for row in cursor.fetchall()}
    
    success = 0
    skipped = 0
    failed = 0
    
    for i, csv_file in enumerate(csv_files, 1):
        race_id = csv_file.stem
        
        # 進捗表示
        if i % 100 == 0 or i == len(csv_files):
            print(f"  進行中... {i}/{len(csv_files)} ({success} 成功 / {skipped} スキップ / {failed} 失敗)")
        
        # 既に登録済みならスキップ
        if race_id in existing_races:
            skipped += 1
            continue
        
        try:
            df = pd.read_csv(csv_file, encoding='utf-8-sig')
            
            # カラム名を英語に正規化
            column_mapping = {
                '着 順': 'finish',
                '馬 番': 'horse_no',
                '人 気': 'popularity'
            }
            df = df.rename(columns=column_mapping)
            
            # horse_idがない場合はスキップ
            if 'horse_id' not in df.columns or df['horse_id'].isna().all():
                skipped += 1
                continue
            
            # レースをracesテーブルに追加
            conn.execute(
                "INSERT OR IGNORE INTO races (race_id, kaisai_date, source) VALUES (?, ?, ?)",
                (race_id, race_id[:8] if len(race_id) >= 8 else None, 'netkeiba')
            )
            
            # resultsテーブルに追加
            for _, row in df.iterrows():
                horse_id = row.get('horse_id')
                if pd.isna(horse_id):
                    continue
                
                try:
                    conn.execute(
                        """INSERT OR REPLACE INTO results 
                        (race_id, horse_id, finish, time, margin, last3f, pass_order, odds, popularity, raw_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            race_id,
                            str(int(horse_id)),
                            row.get('finish'),
                            str(row.get('time')) if pd.notna(row.get('time')) else None,
                            str(row.get('margin')) if pd.notna(row.get('margin')) else None,
                            row.get('last3f'),
                            str(row.get('pass_order')) if pd.notna(row.get('pass_order')) else None,
                            row.get('odds'),
                            row.get('popularity'),
                            None
                        )
                    )
                except Exception:
                    pass
            
            # entriesテーブルにも追加
            for _, row in df.iterrows():
                horse_id = row.get('horse_id')
                if pd.isna(horse_id):
                    continue
                
                try:
                    conn.execute(
                        """INSERT OR REPLACE INTO entries
                        (race_id, horse_id, horse_name, horse_no, bracket, sex, age, handicap, 
                         jockey_id, jockey_name, trainer_id, trainer_name, weight, weight_diff, 
                         odds, popularity, raw_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            race_id,
                            str(int(horse_id)),
                            str(row.get('horse_name')) if pd.notna(row.get('horse_name')) else None,
                            row.get('horse_no'),
                            row.get('bracket'),
                            str(row.get('sex')) if pd.notna(row.get('sex')) else None,
                            row.get('age'),
                            row.get('handicap'),
                            str(int(row.get('jockey_id'))) if pd.notna(row.get('jockey_id')) else None,
                            str(row.get('jockey_name')) if pd.notna(row.get('jockey_name')) else None,
                            str(int(row.get('trainer_id'))) if pd.notna(row.get('trainer_id')) else None,
                            str(row.get('trainer_name')) if pd.notna(row.get('trainer_name')) else None,
                            row.get('weight'),
                            row.get('weight_diff'),
                            row.get('odds'),
                            row.get('popularity'),
                            None
                        )
                    )
                except Exception:
                    pass
            
            conn.commit()
            success += 1
            existing_races.add(race_id)
            
        except Exception as e:
            failed += 1
            if failed <= 5:  # 最初の5個だけエラー表示
                print(f"  ✗ {race_id} - エラー: {e}")
    
    conn.close()
    
    print("\n" + "=" * 80)
    print(f"データベース登録完了")
    print("=" * 80)
    print(f"\n【結果】")
    print(f"  ✅ 新規登録: {success} レース")
    print(f"  ⏭  スキップ: {skipped} レース（既存または無効）")
    print(f"  ❌ 失敗: {failed} レース")
    
    # 最終確認
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM races')
    races_count = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM entries')
    entries_count = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM results')
    results_count = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM results WHERE finish=1')
    win_count = cursor.fetchone()[0]
    conn.close()
    
    print(f"\n【データベース状態】")
    print(f"  📊 レース: {races_count:,}")
    print(f"  🐎 エントリー: {entries_count:,}")
    print(f"  🏁 結果: {results_count:,}")
    print(f"  🥇 1着の馬: {win_count:,}")
    
    if win_count > 0:
        print(f"\n✅ 学習データ準備完了！「2_学習」ページで学習を実行できます")
        return 0
    else:
        print(f"\n⚠️ 1着の馬が0頭です。データ取得に問題がある可能性があります")
        return 1

if __name__ == "__main__":
    sys.exit(register_to_db())
