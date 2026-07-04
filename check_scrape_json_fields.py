"""
JSON blob の中身をパースして、スクレイプ実装とDBデータの整合性を確認
"""
import sys, sqlite3, pathlib, json, collections

DB_PATH = pathlib.Path("keiba/data/keiba_ultimate.db")
con = sqlite3.connect(DB_PATH)
cur = con.cursor()

# ─── 1. races_ultimate の JSON キー集計 ─────────────────
print("=== races_ultimate JSON キー集計 (全件サンプリング1000件) ===")
rows = cur.execute(
    "SELECT data FROM races_ultimate ORDER BY RANDOM() LIMIT 1000"
).fetchall()
race_key_cnt = collections.Counter()
race_key_null = collections.Counter()
for (d,) in rows:
    obj = json.loads(d)
    for k, v in obj.items():
        race_key_cnt[k] += 1
        if v is None or v == "" or v == []:
            race_key_null[k] += 1

n = len(rows)
print(f"  サンプル: {n} 件")
for k in sorted(race_key_cnt.keys()):
    present = race_key_cnt[k]
    null = race_key_null.get(k, 0)
    null_pct = null / present * 100 if present > 0 else 0
    present_pct = present / n * 100
    flag = " ← 欠損多" if null_pct > 30 else ""
    flag2 = " ← 一部なし" if present_pct < 90 else ""
    print(f"  {k:40s}  存在率:{present_pct:5.1f}%  NULL/空率:{null_pct:5.1f}%{flag}{flag2}")

# ─── 2. race_results_ultimate の JSON キー集計 ──────────
print("\n=== race_results_ultimate JSON キー集計 (全件サンプリング1000件) ===")
rows2 = cur.execute(
    "SELECT data FROM race_results_ultimate ORDER BY RANDOM() LIMIT 1000"
).fetchall()
res_key_cnt = collections.Counter()
res_key_null = collections.Counter()
for (d,) in rows2:
    obj = json.loads(d)
    for k, v in obj.items():
        res_key_cnt[k] += 1
        if v is None or v == "" or v == []:
            res_key_null[k] += 1

n2 = len(rows2)
print(f"  サンプル: {n2} 件")
for k in sorted(res_key_cnt.keys()):
    present = res_key_cnt[k]
    null = res_key_null.get(k, 0)
    null_pct = null / present * 100 if present > 0 else 0
    present_pct = present / n2 * 100
    flag = " ← 欠損多" if null_pct > 30 else ""
    flag2 = " ← 一部なし" if present_pct < 90 else ""
    print(f"  {k:40s}  存在率:{present_pct:5.1f}%  NULL/空率:{null_pct:5.1f}%{flag}{flag2}")

# ─── 3. races_ultimate のフルサンプル1件 ────────────────
print("\n=== races_ultimate フル JSON (最新1件) ===")
row = cur.execute(
    "SELECT data FROM races_ultimate ORDER BY race_id DESC LIMIT 1"
).fetchone()
if row:
    obj = json.loads(row[0])
    for k, v in obj.items():
        vstr = str(v)[:120]
        print(f"  {k:40s}: {vstr}")

# ─── 4. race_results_ultimate のフルサンプル1件 ─────────
print("\n=== race_results_ultimate フル JSON (最新1件) ===")
row2 = cur.execute(
    "SELECT data FROM race_results_ultimate ORDER BY id DESC LIMIT 1"
).fetchone()
if row2:
    obj = json.loads(row2[0])
    for k, v in obj.items():
        vstr = str(v)[:120]
        print(f"  {k:40s}: {vstr}")

con.close()
print("\n完了")
