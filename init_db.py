"""
データベースの初期化スクリプト
必要なテーブルを作成する
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "keiba"))

from keiba_ai.config import load_config
from keiba_ai.db import connect, init_db
import sqlite3

def main():
    print("="*60)
    print("データベース初期化")
    print("="*60)
    
    try:
        # 設定読み込み
        config = load_config(str(Path(__file__).parent / "keiba" / "config.yaml"))
        db_path = config.storage.sqlite_path
        
        if not db_path.is_absolute():
            db_path = Path(__file__).parent / "keiba" / db_path
        
        print(f"\nデータベースパス: {db_path}")
        
        # 接続
        conn = connect(db_path)
        
        # テーブル作成
        print("テーブルを作成中...")
        init_db(conn)
        
        # 確認
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = cursor.fetchall()
        
        print(f"\n作成完了: {len(tables)} テーブル")
        for table in tables:
            cursor.execute(f"SELECT COUNT(*) FROM {table[0]}")
            count = cursor.fetchone()[0]
            print(f"  - {table[0]}: {count} レコード")
        
        conn.close()
        print("\n[SUCCESS] データベース初期化完了")
        
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
