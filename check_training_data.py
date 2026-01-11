"""
学習データの確認スクリプト
"""
import sys
from pathlib import Path
import sqlite3

sys.path.insert(0, str(Path(__file__).parent / "keiba"))

from keiba_ai.config import load_config
from keiba_ai.db import connect, load_training_frame

def main():
    print("="*60)
    print("学習データ確認")
    print("="*60)
    
    try:
        config = load_config(str(Path(__file__).parent / "keiba" / "config.yaml"))
        db_path = config.storage.sqlite_path
        
        if not db_path.is_absolute():
            db_path = Path(__file__).parent / "keiba" / db_path
        
        print(f"\nデータベース: {db_path}")
        
        conn = connect(db_path)
        
        # テーブルの確認
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM entries")
        entries_count = cursor.fetchone()[0]
        print(f"entries: {entries_count} レコード")
        
        cursor.execute("SELECT COUNT(*) FROM results")
        results_count = cursor.fetchone()[0]
        print(f"results: {results_count} レコード")
        
        # 学習データの読み込み
        print("\n学習データを読み込み中...")
        df = load_training_frame(conn)
        
        if df is not None and len(df) > 0:
            print(f"\n[SUCCESS] 学習データ読み込み成功")
            print(f"  レコード数: {len(df)}")
            print(f"  ユニークなレース: {df['race_id'].nunique()}")
            print(f"  カラム数: {len(df.columns)}")
            print(f"  カラム: {list(df.columns)}")
            
            # サンプル表示
            print(f"\nサンプルデータ:")
            print(df.head(3))
        else:
            print("\n[ERROR] 学習データが空です")
        
        conn.close()
        
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
