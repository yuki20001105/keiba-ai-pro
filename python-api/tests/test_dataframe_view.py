import csv
import io
import json

from routers import dataframe


def _database(path):
    import sqlite3

    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE races_ultimate (race_id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE race_results_ultimate (id INTEGER PRIMARY KEY, race_id TEXT, data TEXT NOT NULL);
    """)
    connection.execute(
        "INSERT INTO races_ultimate VALUES (?, ?)",
        ("202610310101", json.dumps({"date": "2026-10-31", "venue": "東京", "extra_race_field": "全取得"}, ensure_ascii=False)),
    )
    connection.execute(
        "INSERT INTO races_ultimate VALUES (?, ?)",
        ("202610300101", json.dumps({"date": "2026-10-30", "venue": "京都"}, ensure_ascii=False)),
    )
    connection.executemany(
        "INSERT INTO race_results_ultimate (race_id, data) VALUES (?, ?)",
        [
            ("202610310101", json.dumps({"horse_number": 1, "horse_name": "馬A", "extra_horse_field": {"raw": 1}}, ensure_ascii=False)),
            ("202610310101", json.dumps({"horse_number": 2, "horse_name": "馬B", "unsafe_note": "=1+1"}, ensure_ascii=False)),
            ("202610300101", json.dumps({"horse_number": 1, "horse_name": "馬C"}, ensure_ascii=False)),
        ],
    )
    connection.commit()
    connection.close()


def test_page_uses_union_of_all_saved_columns_and_filters_by_date(tmp_path, monkeypatch):
    path = tmp_path / "keiba_ultimate.db"
    _database(path)
    monkeypatch.setattr(dataframe, "ULTIMATE_DB", path)

    result = dataframe._page("horses", "2026-10-31", "2026-10-31", 100, 0)

    assert result["race_count"] == 1
    assert result["horse_count"] == 2
    assert "extra_horse_field" in result["columns"]
    assert "unsafe_note" in result["columns"]
    assert result["records"][0]["horse_name"] == "馬A"
    assert result["records"][0]["race_id"] == "202610310101"


def test_race_table_page_contains_every_saved_race_column(tmp_path, monkeypatch):
    path = tmp_path / "keiba_ultimate.db"
    _database(path)
    monkeypatch.setattr(dataframe, "ULTIMATE_DB", path)

    result = dataframe._page("races", None, None, 100, 0)

    assert result["total_rows"] == 2
    assert "extra_race_field" in result["columns"]
    assert any(row.get("extra_race_field") == "全取得" for row in result["records"])


def test_csv_contains_all_rows_and_spreadsheet_safe_untrusted_values(tmp_path, monkeypatch):
    path = tmp_path / "keiba_ultimate.db"
    _database(path)
    monkeypatch.setattr(dataframe, "ULTIMATE_DB", path)

    content = "".join(dataframe._csv_stream("horses", None, None))
    assert content.startswith("\ufeff")
    rows = list(csv.DictReader(io.StringIO(content.lstrip("\ufeff"))))
    by_horse = {row["horse_name"]: row for row in rows}

    assert len(rows) == 3
    assert by_horse["馬A"]["extra_horse_field"] == '{"raw":1}'
    assert by_horse["馬B"]["unsafe_note"] == "'=1+1"


def test_read_only_page_does_not_change_database(tmp_path, monkeypatch):
    path = tmp_path / "keiba_ultimate.db"
    _database(path)
    monkeypatch.setattr(dataframe, "ULTIMATE_DB", path)
    before = path.read_bytes()

    dataframe._page("horses", None, None, 1, 0)

    assert path.read_bytes() == before


def test_engineered_feature_page_is_paged_and_keeps_target_separate(monkeypatch):
    import pandas as pd
    from training import db_shared_features

    rows = []
    for day in range(1, 11):
        for horse_number in (1, 2):
            rows.append({
                "race_date": f"202601{day:02d}", "race_id": f"202601{day:02d}0101",
                "venue": "東京", "distance": 1600, "surface": "turf", "num_horses": 2,
                "horse_id": f"H{day}-{horse_number}", "horse_number": horse_number,
                "finish": horse_number, "time_seconds": 90.0 + day + horse_number / 10,
                "odds": 2.0 + horse_number, "popularity": horse_number,
            })
    target = pd.DataFrame(rows)
    features = pd.DataFrame({
        "safe_age": [4.0] * len(rows),
        "safe_prior_starts": list(range(len(rows))),
    })
    monkeypatch.setattr(dataframe, "_feature_inputs", lambda *_: (
        target, features, {"target": {}}, {"cache_hit": True, "point_in_time_validated": True,
                           "snapshot_sha256": "b" * 64}, None, 0.2,
    ))
    prior = pd.DataFrame({"distance": [1600] * 12, "surface": ["turf"] * 12,
                          "time_seconds": [89.0 + day / 10 for day in range(1, 13)]})
    monkeypatch.setattr(db_shared_features, "load_speed_baseline_frame", lambda *_args, **_kwargs: (
        prior, {"date_from": "20250101", "date_to": "20251231", "eligible_rows": len(prior),
                "eligible_races": len(prior), "eligible_dates": 12, "requested_lookback_days": 365},
    ))
    dataframe._SPEED_BASELINE_CACHE.clear()

    result = dataframe._feature_records(
        "2026-01", "2026-01", "local-db-shared-optimized-v1", "speed_deviation", 0.2, 3, 4,
    )

    assert result["total_rows"] == 20
    assert result["race_count"] == 10
    assert result["feature_count"] == 2
    assert len(result["records"]) == 3
    assert result["records"][0]["horse_number"] == 1
    assert "finish" in result["columns"]
    assert "target_value" in result["columns"]
    assert "safe_prior_starts" in result["columns"]
    assert result["point_in_time_validated"] is True


def test_engineered_target_preview_supports_win_place_and_rank(monkeypatch):
    import pandas as pd

    target = pd.DataFrame({
        "race_date": ["20260101"] * 2,
        "race_id": ["R1", "R1"],
        "num_horses": [2, 2],
        "horse_id": ["H1", "H2"],
        "horse_number": [1, 2],
        "finish": [1, 2],
    })
    features = pd.DataFrame({"safe_age": [3.0, 4.0]})
    monkeypatch.setattr(dataframe, "_feature_inputs", lambda *_: (
        target, features, {"target": {}}, {"cache_hit": True}, None, 0.1,
    ))

    win = dataframe._feature_records("2026-01", "2026-01", "local-db-shared-v1", "win", 0.2, 10, 0)
    place = dataframe._feature_records("2026-01", "2026-01", "local-db-shared-v1", "place3", 0.2, 10, 0)
    rank = dataframe._feature_records("2026-01", "2026-01", "local-db-shared-v1", "rank", 0.2, 10, 0)

    assert [row["target_value"] for row in win["records"]] == [1, 0]
    assert [row["target_value"] for row in place["records"]] == [1, 1]
    assert [row["target_value"] for row in rank["records"]] == [2, 1]


def test_speed_deviation_preview_uses_prior_baseline_for_single_display_date(monkeypatch):
    import pandas as pd
    from training import db_shared_features

    target = pd.DataFrame({
        "race_date": ["20261031", "20261031"],
        "race_id": ["202610310101", "202610310101"],
        "venue": ["東京", "東京"], "distance": [1600, 1600],
        "surface": ["turf", "turf"], "num_horses": [2, 2],
        "horse_id": ["H1", "H2"], "horse_number": [1, 2],
        "finish": [1, 2], "time_seconds": [90.0, 91.0],
    })
    prior = pd.DataFrame({
        "race_date": [f"2025{month:02d}01" for month in range(1, 13)],
        "race_id": [f"2025{month:02d}010101" for month in range(1, 13)],
        "distance": [1600] * 12, "surface": ["turf"] * 12,
        "time_seconds": [89.0 + month / 10 for month in range(1, 13)],
    })
    monkeypatch.setattr(dataframe, "_feature_inputs", lambda *_: (
        target, pd.DataFrame({"safe_age": [4.0, 5.0]}), {"target": {}},
        {"cache_hit": True, "point_in_time_validated": True, "snapshot_sha256": "a" * 64}, None, 0.1,
    ))
    monkeypatch.setattr(db_shared_features, "load_speed_baseline_frame", lambda *_args, **_kwargs: (
        prior, {"date_from": "20251101", "date_to": "20261030", "eligible_rows": len(prior),
                "eligible_races": len(prior), "eligible_dates": len(prior),
                "requested_lookback_days": 365},
    ))
    dataframe._SPEED_BASELINE_CACHE.clear()

    result = dataframe._feature_records(
        "2026-10", "2026-10", "local-db-shared-optimized-v1", "speed_deviation", 0.2, 100, 0,
    )

    assert len(result["records"]) == 2
    assert all(row["target_value"] is not None for row in result["records"])
    assert result["target_baseline"]["available"] is True
    assert result["target_baseline"]["date_to"] == "20261030"
    csv_content = "".join(dataframe._feature_csv_stream(
        "2026-10", "2026-10", "local-db-shared-optimized-v1", "speed_deviation", 0.2,
        prepared=(target, pd.DataFrame({"safe_age": [4.0, 5.0]}), {"target": {}},
                  {"snapshot_sha256": "a" * 64}, None, 0.1),
    ))
    csv_rows = list(csv.DictReader(io.StringIO(csv_content.lstrip("\ufeff"))))
    assert len(csv_rows) == 2
    assert all(row["target_value"] for row in csv_rows)


def test_feature_missingness_summary_separates_observed_history_gaps_by_year():
    import pandas as pd

    target = pd.DataFrame({"race_date": ["20250101", "20260101", "20260102"]})
    features = pd.DataFrame({
        "safe_prior_starts": [0, 1, 2],
        "safe_prev_finish": [None, None, 2.0],
        "safe_past5_count": [0, 1, 5],
        "safe_past5_avg_finish": [None, None, 3.0],
        "safe_horse_surface_starts": [0, 1, 2],
        "safe_horse_surface_rank_mean": [None, None, 4.0],
        "safe_weather": [None, "雨", None],
    })

    overall, by_year = dataframe._feature_missingness_summary(target, features)
    summary = {item["feature"]: item for item in overall}
    yearly = {(item["year"], item["feature"]): item for item in by_year}

    assert summary["safe_prev_finish"]["saved_history_gap_count"] == 1
    assert summary["safe_prev_finish"]["unclassified_count"] == 1
    assert summary["safe_past5_avg_finish"]["saved_history_gap_count"] == 1
    assert summary["safe_horse_surface_rank_mean"]["saved_history_gap_count"] == 1
    assert summary["safe_weather"]["unclassified_count"] == 2
    assert yearly[("2026", "safe_prev_finish")]["row_count"] == 2
    assert yearly[("2026", "safe_prev_finish")]["missing_fraction"] == 0.5
    assert summary["safe_weather"]["all_missing"] is False
