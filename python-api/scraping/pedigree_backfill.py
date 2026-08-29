"""Append-only pedigree cache reconciliation for the research SQLite DB."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from scraping.quality import init_acquisition_quality_db


def _count_missing_pedigree_rows(conn: sqlite3.Connection) -> int:
    return int(
        conn.execute(
            """SELECT COUNT(*) FROM race_results_ultimate
               WHERE coalesce(trim(json_extract(data, '$.horse_id')), '') <> ''
                 AND (
                   coalesce(trim(json_extract(data, '$.sire')), '') = '' OR
                   coalesce(trim(json_extract(data, '$.dam')), '') = '' OR
                   coalesce(trim(coalesce(
                     json_extract(data, '$.damsire'),
                     json_extract(data, '$.broodmare_sire')
                   )), '') = ''
                 )"""
        ).fetchone()[0]
    )


def sync_pedigree_cache(
    ultimate_db: Path,
    pedigree_db: Path,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    """Merge cached pedigree into normalized and embedded race-result fields.

    Existing non-empty values always win.  Dry-run is the default and performs
    no writes.  Apply uses one transaction and never deletes source rows.
    """
    if not ultimate_db.exists():
        raise FileNotFoundError(ultimate_db)
    if not pedigree_db.exists():
        raise FileNotFoundError(pedigree_db)

    if apply:
        init_acquisition_quality_db(ultimate_db)
    with sqlite3.connect(str(pedigree_db)) as ped:
        cache_rows = int(
            ped.execute(
                "SELECT COUNT(*) FROM pedigree_cache "
                "WHERE coalesce(trim(sire),'')<>'' OR coalesce(trim(dam),'')<>'' "
                "OR coalesce(trim(damsire),'')<>''"
            ).fetchone()[0]
        )

    with sqlite3.connect(str(ultimate_db)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        missing_before = _count_missing_pedigree_rows(conn)
        existing_normalized = int(
            conn.execute(
                "SELECT COUNT(*) FROM horse_details WHERE "
                "coalesce(trim(sire),'')<>'' OR coalesce(trim(dam),'')<>'' OR "
                "coalesce(trim(damsire),'')<>''"
            ).fetchone()[0]
        )
        if not apply:
            return {
                "mode": "dry-run",
                "cache_rows_available": cache_rows,
                "normalized_rows_before": existing_normalized,
                "embedded_rows_missing_before": missing_before,
                "writes_performed": False,
            }

        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("ATTACH DATABASE ? AS pedigree_source", (str(pedigree_db),))
            conn.execute(
                """INSERT INTO horse_details (horse_id, sire, dam, damsire, updated_at)
                   SELECT horse_id, sire, dam, damsire, datetime('now')
                   FROM pedigree_source.pedigree_cache
                   WHERE coalesce(trim(sire),'')<>'' OR coalesce(trim(dam),'')<>''
                      OR coalesce(trim(damsire),'')<>''
                   ON CONFLICT(horse_id) DO UPDATE SET
                     sire=CASE WHEN coalesce(trim(horse_details.sire),'')=''
                               THEN excluded.sire ELSE horse_details.sire END,
                     dam=CASE WHEN coalesce(trim(horse_details.dam),'')=''
                              THEN excluded.dam ELSE horse_details.dam END,
                     damsire=CASE WHEN coalesce(trim(horse_details.damsire),'')=''
                                  THEN excluded.damsire ELSE horse_details.damsire END,
                     updated_at=datetime('now')"""
            )
            conn.execute(
                """UPDATE race_results_ultimate
                   SET data = json_set(
                     data,
                     '$.sire', coalesce(
                       nullif(trim(json_extract(data, '$.sire')), ''),
                       (SELECT sire FROM pedigree_source.pedigree_cache p
                        WHERE p.horse_id=json_extract(data, '$.horse_id'))
                     ),
                     '$.dam', coalesce(
                       nullif(trim(json_extract(data, '$.dam')), ''),
                       (SELECT dam FROM pedigree_source.pedigree_cache p
                        WHERE p.horse_id=json_extract(data, '$.horse_id'))
                     ),
                     '$.damsire', coalesce(
                       nullif(trim(coalesce(
                         json_extract(data, '$.damsire'),
                         json_extract(data, '$.broodmare_sire')
                       )), ''),
                       (SELECT damsire FROM pedigree_source.pedigree_cache p
                        WHERE p.horse_id=json_extract(data, '$.horse_id'))
                     )
                   )
                   WHERE EXISTS (
                     SELECT 1 FROM pedigree_source.pedigree_cache p
                     WHERE p.horse_id=json_extract(data, '$.horse_id')
                       AND (coalesce(trim(p.sire),'')<>'' OR coalesce(trim(p.dam),'')<>''
                            OR coalesce(trim(p.damsire),'')<>'')
                   )
                     AND (
                       coalesce(trim(json_extract(data, '$.sire')), '')='' OR
                       coalesce(trim(json_extract(data, '$.dam')), '')='' OR
                       coalesce(trim(coalesce(
                         json_extract(data, '$.damsire'),
                         json_extract(data, '$.broodmare_sire')
                       )), '')=''
                     )"""
            )
            conn.execute(
                """UPDATE scrape_repair_queue
                   SET status='completed', updated_at=datetime('now'), last_error=NULL
                   WHERE entity_type='horse' AND repair_kind='pedigree'
                     AND entity_id IN (
                       SELECT horse_id FROM pedigree_source.pedigree_cache
                       WHERE coalesce(trim(sire),'')<>'' OR coalesce(trim(dam),'')<>''
                          OR coalesce(trim(damsire),'')<>''
                     )"""
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            try:
                conn.execute("DETACH DATABASE pedigree_source")
            except sqlite3.Error:
                pass

        missing_after = _count_missing_pedigree_rows(conn)
        normalized_after = int(
            conn.execute(
                "SELECT COUNT(*) FROM horse_details WHERE "
                "coalesce(trim(sire),'')<>'' OR coalesce(trim(dam),'')<>'' OR "
                "coalesce(trim(damsire),'')<>''"
            ).fetchone()[0]
        )
        quick_check = str(conn.execute("PRAGMA quick_check").fetchone()[0])

    return {
        "mode": "apply",
        "cache_rows_available": cache_rows,
        "normalized_rows_before": existing_normalized,
        "normalized_rows_after": normalized_after,
        "embedded_rows_missing_before": missing_before,
        "embedded_rows_missing_after": missing_after,
        "embedded_rows_repaired": max(0, missing_before - missing_after),
        "writes_performed": True,
        "quick_check": quick_check,
    }
