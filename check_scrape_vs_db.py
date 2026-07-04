"""
スクレイプ実装 vs DB 実データ の整合性確認スクリプト
"""
import sys, sqlite3, pathlib, json, textwrap

DB_PATH = pathlib.Path("keiba/data/keiba_ultimate.db")
con = sqlite3.connect(DB_PATH)
cur = con.cursor()

# ─── 1. テーブル一覧 ──────────────────────────────────────
print("=== テーブル一覧 ===")
tables = cur.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
).fetchall()
for (t,) in tables:
    cnt = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"  {t}: {cnt:,} rows")

# ─── 2. races_ultimate 列一覧 ───────────────────────────
print("\n=== races_ultimate 列 ===")
races_cols = [r[1] for r in cur.execute("PRAGMA table_info(races_ultimate)").fetchall()]
print(" ", races_cols)

# ─── 3. race_results_ultimate 列一覧 ────────────────────
print("\n=== race_results_ultimate 列 ===")
results_cols = [r[1] for r in cur.execute("PRAGMA table_info(race_results_ultimate)").fetchall()]
print(" ", results_cols)

# ─── 4. return_tables_ultimate 列 ───────────────────────
print("\n=== return_tables_ultimate 列 ===")
try:
    ret_cols = [r[1] for r in cur.execute("PRAGMA table_info(return_tables_ultimate)").fetchall()]
    cnt_ret = cur.execute("SELECT COUNT(*) FROM return_tables_ultimate").fetchone()[0]
    print(f"  {cnt_ret:,} rows: {ret_cols}")
except Exception as e:
    print(f"  なし ({e})")

# ─── 5. speed_figures 列 ────────────────────────────────
print("\n=== speed_figures 列 ===")
try:
    sf_cols = [r[1] for r in cur.execute("PRAGMA table_info(speed_figures)").fetchall()]
    cnt_sf = cur.execute("SELECT COUNT(*) FROM speed_figures").fetchone()[0]
    print(f"  {cnt_sf:,} rows: {sf_cols}")
except Exception as e:
    print(f"  なし ({e})")

# ─── 6. races_ultimate サンプル1行の全カラム値 ──────────
print("\n=== races_ultimate サンプル (最新1件) ===")
row = cur.execute(
    "SELECT * FROM races_ultimate ORDER BY race_id DESC LIMIT 1"
).fetchone()
if row:
    for col, val in zip(races_cols, row):
        display = str(val)[:80] if val is not None else "NULL"
        print(f"  {col:30s}: {display}")

# ─── 7. race_results_ultimate サンプル1行 ───────────────
print("\n=== race_results_ultimate サンプル (最新1件) ===")
row2 = cur.execute(
    "SELECT * FROM race_results_ultimate ORDER BY race_id DESC LIMIT 1"
).fetchone()
if row2:
    for col, val in zip(results_cols, row2):
        display = str(val)[:80] if val is not None else "NULL"
        print(f"  {col:30s}: {display}")

# ─── 8. race_results_ultimate の NULL率確認 ─────────────
print("\n=== race_results_ultimate 各列 NULL率 (全行) ===")
total = cur.execute("SELECT COUNT(*) FROM race_results_ultimate").fetchone()[0]
null_stats = []
for col in results_cols:
    null_cnt = cur.execute(
        f"SELECT COUNT(*) FROM race_results_ultimate WHERE {col} IS NULL"
    ).fetchone()[0]
    pct = null_cnt / total * 100 if total > 0 else 0
    null_stats.append((col, null_cnt, pct))

for col, nc, pct in sorted(null_stats, key=lambda x: -x[2]):
    bar = "#" * int(pct / 5)
    print(f"  {col:35s}: {pct:5.1f}% NULL  ({nc:,}/{total:,}) {bar}")

# ─── 9. races_ultimate の NULL率確認 ────────────────────
print("\n=== races_ultimate 各列 NULL率 (全行) ===")
total_r = cur.execute("SELECT COUNT(*) FROM races_ultimate").fetchone()[0]
for col in races_cols:
    null_cnt = cur.execute(
        f"SELECT COUNT(*) FROM races_ultimate WHERE {col} IS NULL"
    ).fetchone()[0]
    pct = null_cnt / total_r * 100 if total_r > 0 else 0
    if pct > 0:
        print(f"  {col:30s}: {pct:5.1f}% NULL ({null_cnt:,}/{total_r:,})")

print("\n  ※ NULL率 0% の列は省略")

con.close()
print("\n完了")
