"""Ultimate版データベースの内容確認"""
import sqlite3
import sys
from pathlib import Path

db_path = Path("keiba/data/keiba_ultimate.db")

if not db_path.exists():
    print("✗ Ultimate DB未作成")
    sys.exit(1)

conn = sqlite3.connect(db_path)
c = conn.cursor()

# テーブル一覧
c.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [t[0] for t in c.fetchall()]
print(f"✓ テーブル数: {len(tables)}")
print(f"  {', '.join(tables)}\n")

# 各テーブルのレコード数
for table in tables:
    if table != 'sqlite_sequence':
        try:
            c.execute(f"SELECT COUNT(*) FROM {table}")
            count = c.fetchone()[0]
            print(f"  {table}: {count} レコード")
        except:
            pass

# entriesテーブルのカラム構造
print("\n【entriesテーブルの特徴量】")
c.execute("PRAGMA table_info(entries)")
cols = c.fetchall()
print(f"カラム数: {len(cols)}\n")

# カラム名を表示
for i, col in enumerate(cols, 1):
    col_name = col[1]
    col_type = col[2]
    print(f"{i:3d}. {col_name:30s} ({col_type})")

# サンプルデータがあれば1件表示
c.execute("SELECT COUNT(*) FROM entries")
if c.fetchone()[0] > 0:
    print("\n【サンプルデータ（1件目）】")
    c.execute("SELECT * FROM entries LIMIT 1")
    row = c.fetchone()
    for i, col in enumerate(cols):
        col_name = col[1]
        value = row[i] if row else None
        if value is not None:
            print(f"  {col_name}: {value}")

conn.close()
