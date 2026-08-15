from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Sequence


def audit(database: Path, *, minimum_annual_races: int = 2_500) -> dict[str, object]:
    database = database.resolve()
    if not database.is_file():
        raise ValueError(f"database does not exist: {database}")
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "race_results_ultimate" not in tables:
            raise ValueError("race_results_ultimate is missing")
        legacy = connection.execute("""
            SELECT substr(race_id, 1, 4) AS year,
                   COUNT(*) AS entries,
                   COUNT(DISTINCT race_id) AS races,
                   SUM(CASE WHEN json_type(data, '$.odds_observed_at') IS NOT NULL
                            THEN 1 ELSE 0 END) AS timestamped_odds_entries
            FROM race_results_ultimate
            WHERE substr(race_id, 1, 4) BETWEEN '2019' AND '2024'
            GROUP BY substr(race_id, 1, 4)
            ORDER BY year
            """).fetchall()
        licensed: list[tuple[object, ...]] = []
        if "licensed_history_entries" in tables:
            licensed = connection.execute("""
                SELECT substr(race_date, 1, 4) AS year,
                       COUNT(*) AS entries,
                       COUNT(DISTINCT race_id) AS races,
                       COUNT(*) AS timestamped_odds_entries
                FROM licensed_history_entries
                WHERE substr(race_date, 1, 4) BETWEEN '2019' AND '2024'
                GROUP BY substr(race_date, 1, 4)
                ORDER BY year
                """).fetchall()
        official: list[tuple[object, ...]] = []
        if "official_history_entries" in tables:
            official = connection.execute("""
                SELECT substr(race_date, 1, 4) AS year,
                       COUNT(*) AS entries,
                       COUNT(DISTINCT race_id) AS races
                FROM official_history_entries
                WHERE substr(race_date, 1, 4) BETWEEN '2019' AND '2024'
                GROUP BY substr(race_date, 1, 4)
                ORDER BY year
                """).fetchall()
        authorized_races: list[tuple[object, ...]] = []
        unions: list[str] = []
        if "official_history_entries" in tables:
            unions.append("SELECT race_date, race_id FROM official_history_entries")
        if "licensed_history_entries" in tables:
            unions.append("SELECT race_date, race_id FROM licensed_history_entries")
        if unions:
            authorized_races = connection.execute(
                "SELECT substr(race_date, 1, 4), COUNT(DISTINCT race_id) FROM ("
                + " UNION ALL ".join(unions)
                + ") WHERE substr(race_date, 1, 4) BETWEEN '2019' AND '2024' "
                "GROUP BY substr(race_date, 1, 4) ORDER BY 1"
            ).fetchall()
    years: dict[str, dict[str, int]] = {
        str(year): {
            "legacy_entries": 0,
            "legacy_races": 0,
            "legacy_timestamped_odds_entries": 0,
            "licensed_entries": 0,
            "licensed_races": 0,
            "licensed_timestamped_odds_entries": 0,
            "official_result_entries": 0,
            "official_result_races": 0,
            "authorized_outcome_races": 0,
        }
        for year in range(2019, 2025)
    }
    for year, entries, races, timestamped in legacy:
        years[str(year)].update(
            {
                "legacy_entries": int(entries),
                "legacy_races": int(races),
                "legacy_timestamped_odds_entries": int(timestamped or 0),
            }
        )
    for year, entries, races, timestamped in licensed:
        years[str(year)].update(
            {
                "licensed_entries": int(entries),
                "licensed_races": int(races),
                "licensed_timestamped_odds_entries": int(timestamped or 0),
            }
        )
    for year, entries, races in official:
        years[str(year)].update(
            {
                "official_result_entries": int(entries),
                "official_result_races": int(races),
            }
        )
    for year, races in authorized_races:
        years[str(year)]["authorized_outcome_races"] = int(races)
    licensed_entries = sum(year["licensed_entries"] for year in years.values())
    timestamped_entries = sum(
        year["licensed_timestamped_odds_entries"] for year in years.values()
    )
    return {
        "schema": "phase3n-research-data-readiness-v1",
        "database": str(database),
        "target_years": [2019, 2020, 2021, 2022, 2023, 2024],
        "coverage_by_year": years,
        "licensed_history_table_present": "licensed_history_entries" in tables,
        "official_history_table_present": "official_history_entries" in tables,
        "licensed_entry_count": licensed_entries,
        "official_result_entry_count": sum(
            year["official_result_entries"] for year in years.values()
        ),
        "point_in_time_licensed_entry_count": timestamped_entries,
        "minimum_annual_races": minimum_annual_races,
        "ready_for_continuous_walk_forward": all(
            year["authorized_outcome_races"] >= minimum_annual_races
            for year in years.values()
        ),
        "ready_for_speed_deviation_walk_forward": all(
            year["authorized_outcome_races"] >= minimum_annual_races
            for year in years.values()
        ),
        "ready_for_oof_value_evaluation": (
            licensed_entries > 0 and timestamped_entries == licensed_entries
        ),
        "database_modified": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit Phase3N historical research data readiness."
    )
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--minimum-annual-races", type=int, default=2_500)
    args = parser.parse_args(argv)
    print(
        json.dumps(
            audit(args.db, minimum_annual_races=args.minimum_annual_races),
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
