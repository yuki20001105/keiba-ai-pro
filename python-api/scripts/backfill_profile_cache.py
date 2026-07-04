"""
race_results_ultimate → pedigree_cache 一括バックフィルスクリプト。

race_results_ultimate の JSON に保存済みの horse_birth_date / sire / dam 等を
pedigree_cache テーブルへ一括転記する（一回限りの実行）。

既存行は「より新しい値で上書き」する: 空でない値が来た場合のみ更新。

使用方法:
    python-api\.venv\Scripts\python.exe python-api/scripts/backfill_profile_cache.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = ROOT / "keiba" / "data" / "keiba_ultimate.db"
CACHE_PATH = ROOT / "keiba" / "data" / "pedigree_cache.db"


def backfill() -> None:
    if not DB_PATH.exists():
        print(f"ERROR: DB not found: {DB_PATH}")
        sys.exit(1)
    if not CACHE_PATH.exists():
        print(f"ERROR: pedigree_cache not found: {CACHE_PATH}")
        sys.exit(1)

    print(f"Source DB  : {DB_PATH}")
    print(f"Cache DB   : {CACHE_PATH}")
    print("Extracting horse profiles from race_results_ultimate ...")

    conn_u = sqlite3.connect(str(DB_PATH))
    conn_u.row_factory = sqlite3.Row

    # 最新レースを優先（race_id 降順）するため ORDER BY DESC で取得し、
    # horse_id ごとに最初に出現した行（= 最新レース）を採用する
    rows = conn_u.execute("""
        SELECT
            json_extract(data, '$.horse_id')            AS horse_id,
            json_extract(data, '$.sire')                AS sire,
            json_extract(data, '$.dam')                 AS dam,
            json_extract(data, '$.damsire')             AS damsire,
            json_extract(data, '$.horse_birth_date')    AS birth_date,
            json_extract(data, '$.horse_owner')         AS owner,
            json_extract(data, '$.horse_breeder')       AS breeder,
            json_extract(data, '$.horse_breeding_farm') AS breeding_farm,
            json_extract(data, '$.coat_color')          AS coat_color
        FROM race_results_ultimate
        WHERE json_extract(data, '$.horse_id') IS NOT NULL
          AND json_extract(data, '$.horse_id') != ''
        ORDER BY race_id DESC
    """).fetchall()
    conn_u.close()

    print(f"Total rows in race_results_ultimate : {len(rows)}")

    # horse_id ごとに最新（先頭）行を採用（birth_date 有無を優先）
    horse_map: dict[str, tuple] = {}
    for r in rows:
        hid = r["horse_id"]
        if not hid:
            continue
        existing = horse_map.get(hid)
        bd = r["birth_date"] or ""
        if existing is None or (bd and not existing[4]):
            horse_map[hid] = (
                hid,
                r["sire"] or "",
                r["dam"] or "",
                r["damsire"] or "",
                bd,
                r["owner"] or "",
                r["breeder"] or "",
                r["breeding_farm"] or "",
                r["coat_color"] or "",
            )

    print(f"Unique horses found                 : {len(horse_map)}")

    conn_c = sqlite3.connect(str(CACHE_PATH))
    conn_c.execute("PRAGMA journal_mode=WAL")
    conn_c.execute("PRAGMA synchronous=NORMAL")

    before_total = conn_c.execute("SELECT COUNT(*) FROM pedigree_cache").fetchone()[0]
    before_bd    = conn_c.execute(
        "SELECT COUNT(*) FROM pedigree_cache WHERE birth_date IS NOT NULL AND length(birth_date) > 0"
    ).fetchone()[0]
    print(f"pedigree_cache BEFORE : total={before_total}, with birth_date={before_bd}")

    batch = list(horse_map.values())
    CHUNK = 5000
    inserted = 0
    for i in range(0, len(batch), CHUNK):
        chunk = batch[i : i + CHUNK]
        conn_c.executemany("""
            INSERT INTO pedigree_cache
                (horse_id, sire, dam, damsire, birth_date, owner, breeder, breeding_farm, coat_color)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(horse_id) DO UPDATE SET
                sire          = CASE WHEN length(excluded.sire)          > 0 THEN excluded.sire          ELSE sire          END,
                dam           = CASE WHEN length(excluded.dam)           > 0 THEN excluded.dam           ELSE dam           END,
                damsire       = CASE WHEN length(excluded.damsire)       > 0 THEN excluded.damsire       ELSE damsire       END,
                birth_date    = CASE WHEN length(excluded.birth_date)    > 0 THEN excluded.birth_date    ELSE birth_date    END,
                owner         = CASE WHEN length(excluded.owner)         > 0 THEN excluded.owner         ELSE owner         END,
                breeder       = CASE WHEN length(excluded.breeder)       > 0 THEN excluded.breeder       ELSE breeder       END,
                breeding_farm = CASE WHEN length(excluded.breeding_farm) > 0 THEN excluded.breeding_farm ELSE breeding_farm END,
                coat_color    = CASE WHEN length(excluded.coat_color)    > 0 THEN excluded.coat_color    ELSE coat_color    END
        """, chunk)
        conn_c.commit()
        inserted += len(chunk)
        print(f"  ... {inserted}/{len(batch)} processed", end="\r", flush=True)

    after_total = conn_c.execute("SELECT COUNT(*) FROM pedigree_cache").fetchone()[0]
    after_bd    = conn_c.execute(
        "SELECT COUNT(*) FROM pedigree_cache WHERE birth_date IS NOT NULL AND length(birth_date) > 0"
    ).fetchone()[0]
    conn_c.close()

    print()
    print(f"pedigree_cache AFTER  : total={after_total}  (+{after_total - before_total})")
    print(f"  with birth_date     : {after_bd}  (+{after_bd - before_bd})")
    print("Done!")


if __name__ == "__main__":
    backfill()
