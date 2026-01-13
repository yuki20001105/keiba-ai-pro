import sqlite3

# Ultimate DB
db_path = r'C:\Users\yuki2\Documents\ws\keiba-ai-pro\keiba\data\keiba_ultimate.db'
print(f"\n{'='*60}")
print(f"Checking: {db_path}")
print(f"{'='*60}")

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()

print(f"\nTables found: {len(tables)}")
for table in tables:
    table_name = table[0]
    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    count = cursor.fetchone()[0]
    print(f"  {table_name}: {count:,} rows")
    
    if count > 0:
        cursor.execute(f"SELECT * FROM {table_name} LIMIT 1")
        cols = [desc[0] for desc in cursor.description]
        print(f"    Columns ({len(cols)}): {', '.join(cols[:10])}...")

conn.close()

# Normal DB
db_path2 = r'C:\Users\yuki2\Documents\ws\keiba-ai-pro\keiba\data\keiba.db'
print(f"\n{'='*60}")
print(f"Checking: {db_path2}")
print(f"{'='*60}")

conn2 = sqlite3.connect(db_path2)
cursor2 = conn2.cursor()

cursor2.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables2 = cursor2.fetchall()

print(f"\nTables found: {len(tables2)}")
for table in tables2:
    table_name = table[0]
    cursor2.execute(f"SELECT COUNT(*) FROM {table_name}")
    count = cursor2.fetchone()[0]
    print(f"  {table_name}: {count:,} rows")

conn2.close()
