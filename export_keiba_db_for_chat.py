import sqlite3
import json
import csv
from pathlib import Path

DB = Path(r"C:\Users\yuki2\Documents\ws\keiba-ai-pro\keiba\data\keiba_ultimate.db")
OUT = Path(r"C:\Users\yuki2\Documents\ws\keiba-ai-pro\keiba\data\db_export_for_chat")
OUT.mkdir(parents=True, exist_ok=True)

conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
conn.execute("PRAGMA query_only = ON")

tables = [
    row[0]
    for row in conn.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type='table'
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
    """)
]

summary = []

for table in tables:
    safe_name = table.replace("/", "_").replace("\\", "_")

    # カラム情報
    columns = conn.execute(f'PRAGMA table_info("{table}")').fetchall()

    # 件数
    try:
        count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    except Exception as e:
        count = None

    summary.append({
        "table": table,
        "row_count": count,
        "columns": [
            {
                "cid": c[0],
                "name": c[1],
                "type": c[2],
                "notnull": c[3],
                "default": c[4],
                "pk": c[5],
            }
            for c in columns
        ],
    })

    # 各テーブル最大50行だけCSV出力
    try:
        cur = conn.execute(f'SELECT * FROM "{table}" LIMIT 50')
        rows = cur.fetchall()
        headers = [d[0] for d in cur.description]

        with open(OUT / f"{safe_name}_sample.csv", "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(rows)

    except Exception as e:
        with open(OUT / f"{safe_name}_ERROR.txt", "w", encoding="utf-8") as f:
            f.write(str(e))

with open(OUT / "database_schema_summary.json", "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)

with open(OUT / "table_counts.csv", "w", newline="", encoding="utf-8-sig") as f:
    writer = csv.writer(f)
    writer.writerow(["table", "row_count", "column_count"])
    for item in summary:
        writer.writerow([
            item["table"],
            item["row_count"],
            len(item["columns"]),
        ])

conn.close()

print(f"Export complete: {OUT}")
print(f"Tables: {len(tables)}")
