import pandas as pd

from routers.predict_data_refresh import (
    merge_fresh_pre_race_data,
    race_metadata_invalid,
)


def test_invalid_distance_requires_refresh() -> None:
    assert race_metadata_invalid(pd.DataFrame({"distance": [0, 0]}))
    assert race_metadata_invalid(pd.DataFrame({"distance": [None, None]}))
    assert not race_metadata_invalid(pd.DataFrame({"distance": [1000, 1000]}))


def test_merge_refreshes_pre_race_fields_without_result_leakage() -> None:
    df = pd.DataFrame(
        {
            "horse_number": [1, 2],
            "horse_name": ["broken-1", "broken-2"],
            "distance": [0, 0],
            "odds": [None, None],
        }
    )
    race_info = {"distance": 0, "track_type": ""}
    fresh = {
        "race_info": {
            "race_name": "3歳未勝利",
            "distance": 1000,
            "track_type": "ダート",
            "venue": "札幌",
        },
        "horses": [
            {
                "horse_number": 1,
                "horse_name": "一番馬",
                "odds": 4.2,
                "popularity": 2,
                "finish_position": 1,
            },
            {
                "horse_number": 2,
                "horse_name": "二番馬",
                "odds": 2.8,
                "popularity": 1,
                "finish_position": 2,
            },
        ],
    }

    changed = merge_fresh_pre_race_data(df, race_info, fresh)

    assert changed
    assert df["distance"].tolist() == [1000, 1000]
    assert df["surface"].tolist() == ["ダート", "ダート"]
    assert df["horse_name"].tolist() == ["一番馬", "二番馬"]
    assert df["odds"].tolist() == [4.2, 2.8]
    assert df["popularity"].tolist() == [2.0, 1.0]
    assert "finish_position" not in df.columns
    assert race_info["distance"] == 1000
