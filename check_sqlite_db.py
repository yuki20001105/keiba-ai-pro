import sys
sys.path.insert(0, r"C:\Users\yuki2\Documents\ws\keiba-ai-pro\keiba")
from keiba_ai.db import connect

try:
    conn = connect()
    cursor = conn.cursor()
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = cursor.fetchall()
    print(f"✅ SQLiteデータベース接続成功")
    print(f"   テーブル数: {len(tables)}")
    
    for table in tables:
        table_name = table[0]
        cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
        count = cursor.fetchone()[0]
        print(f"   - {table_name}: {count:,} レコード")
    
    conn.close()
except Exception as e:
    print(f"❌ エラー: {e}")
