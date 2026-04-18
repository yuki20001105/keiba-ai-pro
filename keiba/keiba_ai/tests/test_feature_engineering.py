"""
特徴量エンジニアリング関数の単体テスト。
DB 不要のピュアロジックテストのみを含む。

実行:
    pytest keiba/keiba_ai/tests/test_feature_engineering.py -v
    pytest keiba/keiba_ai/tests/test_feature_engineering.py -v -s  # print 表示付き
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# パス設定（パッケージ外から実行された場合でも import できるように）
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent.parent))  # keiba/ を追加
sys.path.insert(0, str(_HERE.parent.parent.parent.parent / "python-api"))  # python-api/ を追加

from keiba_ai.feature_engineering import (  # type: ignore
    _dist_band,
    parse_race_time_to_seconds,
    add_derived_features,
    _fe_id_season,
    _fe_market,
    _fe_prev_race,
    _fe_missing_flags,
    get_course_features,
    extract_race_info,
)
from keiba_ai.train import safe_int_convert  # type: ignore


# ===========================================================================
# parse_race_time_to_seconds
# ===========================================================================

class TestParseRaceTimeToSeconds:
    @pytest.mark.parametrize("input_val, expected", [
        ("1:34.5",  94.5),
        ("1:34",    94.0),
        ("0:58.3",  58.3),
        (94.5,      94.5),
        (94,        94.0),
        ("94.5",    94.5),
        (None,      float("nan")),
        ("",        float("nan")),
        ("-",       float("nan")),
        ("nan",     float("nan")),
        ("2:00.0", 120.0),
    ])
    def test_parse(self, input_val, expected):
        result = parse_race_time_to_seconds(input_val)
        if math.isnan(expected):
            assert math.isnan(result), f"Expected NaN for input {input_val!r}, got {result}"
        else:
            assert abs(result - expected) < 1e-9, (
                f"parse_race_time_to_seconds({input_val!r}) = {result}, expected {expected}"
            )

    def test_invalid_format_returns_nan(self):
        """不正フォーマット（コロンあり・数字なし）は NaN を返す"""
        assert math.isnan(parse_race_time_to_seconds("abc:def"))

    def test_float_nan_returns_nan(self):
        assert math.isnan(parse_race_time_to_seconds(float("nan")))


# ===========================================================================
# _dist_band
# ===========================================================================

class TestDistBand:
    @pytest.mark.parametrize("distance, expected", [
        (1000, "sprint"),
        (1200, "sprint"),
        (1201, "mile"),
        (1600, "mile"),
        (1601, "middle"),
        (2200, "middle"),
        (2201, "long"),
        (3600, "long"),
    ])
    def test_dist_band(self, distance, expected):
        assert _dist_band(distance) == expected

    def test_none_returns_unknown(self):
        assert _dist_band(None) == "unknown"

    def test_string_number(self):
        """文字列数値でも動作する"""
        assert _dist_band("1800") == "middle"

    def test_invalid_returns_unknown(self):
        assert _dist_band("abc") == "unknown"


# ===========================================================================
# safe_int_convert（train.py）
# ===========================================================================

class TestSafeIntConvert:
    @pytest.mark.parametrize("input_val, expected", [
        (1,        1),
        ("3",      3),
        ("1.0",    1),
        (None,     None),
        (float("nan"), None),
        ("取消",   None),
        ("欠場",   None),
        ("中止",   None),
        ("除外",   None),
        ("失格",   None),
        ("-",      None),
        ("",       None),
        ("取",     None),
    ])
    def test_safe_int_convert(self, input_val, expected):
        assert safe_int_convert(input_val) == expected, (
            f"safe_int_convert({input_val!r}) = {safe_int_convert(input_val)!r}, expected {expected!r}"
        )

    def test_float_value(self):
        assert safe_int_convert(3.7) == 3


# ===========================================================================
# extract_race_info
# ===========================================================================

class TestExtractRaceInfo:
    def test_standard_race_id(self):
        info = extract_race_info("202505060301")
        assert info["venue_code"] == "03"
        assert info["race_num"] == 1
        assert info["date"] == "20250506"

    def test_invalid_length_returns_nones(self):
        info = extract_race_info("12345")
        assert info["venue_code"] is None
        assert info["race_num"] is None
        assert info["date"] is None


# ===========================================================================
# _fe_id_season
# ===========================================================================

class TestFeIdSeason:
    def _make_df(self, race_id: str, surface: str = "ダート", bracket: int = 3) -> pd.DataFrame:
        return pd.DataFrame([{
            "race_id":  race_id,
            "horse_id": "H001",
            "sex":      "牡",
            "surface":  surface,
            "bracket":  bracket,
            "race_date": race_id[:8],
        }])

    def test_venue_code_extracted(self):
        df = _fe_id_season(self._make_df("202505060301"))
        assert df["venue_code"].iloc[0] == "03"

    def test_race_num_extracted(self):
        df = _fe_id_season(self._make_df("202505060312"))
        assert df["race_num"].iloc[0] == 12

    def test_n_horses_single_row(self):
        df = _fe_id_season(self._make_df("202505060301"))
        assert df["n_horses"].iloc[0] == 1

    def test_n_horses_multiple_entries_same_race(self):
        base = "202505060301"
        rows = [{"race_id": base, "horse_id": f"H{i:03d}", "sex": "牡",
                 "surface": "芝", "bracket": i, "race_date": base[:8]} for i in range(1, 9)]
        df = pd.DataFrame(rows)
        df = _fe_id_season(df)
        assert (df["n_horses"] == 8).all()

    def test_cos_sin_date_in_range(self):
        df = _fe_id_season(self._make_df("202501010301"))
        assert -1.0 <= df["cos_date"].iloc[0] <= 1.0
        assert -1.0 <= df["sin_date"].iloc[0] <= 1.0

    def test_sex_code_male(self):
        df = _fe_id_season(self._make_df("202505060301"))
        assert df["sex_code"].iloc[0] == -1.0

    def test_sex_code_female(self):
        df = _fe_id_season(
            pd.DataFrame([{"race_id": "202505060301", "horse_id": "H001",
                           "sex": "牝", "surface": "芝", "bracket": 2, "race_date": "20250506"}])
        )
        assert df["sex_code"].iloc[0] == 1.0

    def test_frame_race_type_dirt_inner(self):
        """ダート × 内枠 = 正の値"""
        df = _fe_id_season(self._make_df("202505060301", surface="ダート", bracket=2))
        assert df["frame_race_type"].iloc[0] > 0

    def test_frame_race_type_turf_outer(self):
        """芝 × 外枠 = 負の値"""
        df = _fe_id_season(self._make_df("202505060301", surface="芝", bracket=8))
        assert df["frame_race_type"].iloc[0] < 0


# ===========================================================================
# _fe_market
# ===========================================================================

class TestFeMarket:
    def _make_race(self, odds: list[float | None], race_id: str = "202505060301") -> pd.DataFrame:
        rows = [{"race_id": race_id, "horse_id": f"H{i:03d}", "odds": o}
                for i, o in enumerate(odds, start=1)]
        return pd.DataFrame(rows)

    def test_implied_prob_calculated(self):
        df = _fe_market(self._make_race([2.0, 4.0, 8.0]))
        assert abs(df["implied_prob"].iloc[0] - 0.5) < 1e-9

    def test_implied_prob_norm_sums_to_one(self):
        df = _fe_market(self._make_race([2.0, 4.0, 8.0]))
        assert abs(df["implied_prob_norm"].sum() - 1.0) < 1e-6

    def test_odds_rank_lowest_is_1(self):
        """最低オッズが人気1位"""
        df = _fe_market(self._make_race([2.0, 4.0, 8.0]))
        assert df["odds_rank_in_race"].min() == 1.0

    def test_odds_is_missing_flag(self):
        df = _fe_market(self._make_race([2.0, None, 8.0]))
        assert df["odds_is_missing"].iloc[1] == 1
        assert df["odds_is_missing"].iloc[0] == 0

    def test_market_entropy_positive(self):
        df = _fe_market(self._make_race([2.0, 4.0, 8.0, 16.0]))
        assert df["market_entropy"].iloc[0] > 0.0

    def test_no_odds_column_passthrough(self):
        """odds 列がなければ元の DataFrame をそのまま返す"""
        df = pd.DataFrame([{"race_id": "202505060301", "horse_id": "H001"}])
        result = _fe_market(df)
        assert "implied_prob" not in result.columns


# ===========================================================================
# _fe_prev_race
# ===========================================================================

class TestFePrevRace:
    def test_speed_index_calculated(self):
        df = pd.DataFrame([{
            "race_id":           "202505060301",
            "horse_id":          "H001",
            "prev_race_time":    "1:34.5",   # 94.5秒
            "prev_race_distance": 1800,
            "surface":           "芝",
        }])
        df = _fe_prev_race(df)
        expected = 1800 / 94.5
        assert abs(df["prev_speed_index"].iloc[0] - expected) < 1e-6

    def test_days_since_last_race_from_prev_race_date(self):
        df = pd.DataFrame([{
            "race_id":        "202505100301",
            "horse_id":       "H001",
            "race_date":      "20250510",
            "prev_race_date": "20250420",   # 20 日前
        }])
        df = _fe_prev_race(df)
        assert df["days_since_last_race"].iloc[0] == 20

    def test_negative_days_becomes_nan(self):
        """prev_race_date が race_date より後 → NaN"""
        df = pd.DataFrame([{
            "race_id":        "202505100301",
            "horse_id":       "H001",
            "race_date":      "20250410",
            "prev_race_date": "20250420",
        }])
        df = _fe_prev_race(df)
        assert pd.isna(df["days_since_last_race"].iloc[0])

    def test_horse_win_rate_calculated(self):
        df = pd.DataFrame([{
            "race_id":          "202505060301",
            "horse_id":         "H001",
            "horse_total_runs": 10,
            "horse_total_wins": 3,
        }])
        df = _fe_prev_race(df)
        assert abs(df["horse_win_rate"].iloc[0] - 0.3) < 1e-9

    def test_zero_runs_gives_nan(self):
        df = pd.DataFrame([{
            "race_id":          "202505060301",
            "horse_id":         "H001",
            "horse_total_runs": 0,
            "horse_total_wins": 0,
        }])
        df = _fe_prev_race(df)
        assert pd.isna(df["horse_win_rate"].iloc[0])


# ===========================================================================
# _fe_missing_flags
# ===========================================================================

class TestFeMissingFlags:
    def test_missing_flag_set_for_nan(self):
        df = pd.DataFrame([{
            "race_id":         "202505060301",
            "prev_race_finish": None,
            "days_since_last_race": None,
        }])
        df = _fe_missing_flags(df)
        assert df["prev_race_finish_is_missing"].iloc[0] == 1
        assert df["days_since_last_race_is_missing"].iloc[0] == 1

    def test_no_missing_flag_for_valid_value(self):
        df = pd.DataFrame([{
            "race_id":          "202505060301",
            "prev_race_finish": "3",
        }])
        df = _fe_missing_flags(df)
        assert df["prev_race_finish_is_missing"].iloc[0] == 0
        assert df["prev_race_finish"].iloc[0] == 3.0

    def test_cancelled_horse_string_becomes_nan_with_flag(self):
        """'中止' などの文字列は NaN に変換されフラグが立つ"""
        df = pd.DataFrame([{
            "race_id":          "202505060301",
            "prev_race_finish": "中止",
        }])
        df = _fe_missing_flags(df)
        assert df["prev_race_finish_is_missing"].iloc[0] == 1
        assert pd.isna(df["prev_race_finish"].iloc[0])


# ===========================================================================
# add_derived_features — 結合テスト（DB 不要）
# ===========================================================================

class TestAddDerivedFeaturesIntegration:
    """DB なしで add_derived_features の基本動作を確認する。"""

    def _make_minimal_df(self) -> pd.DataFrame:
        """最小限の列を持つ DataFrame を生成する"""
        return pd.DataFrame([
            {
                "race_id":   "202505060301",
                "horse_id":  "H001",
                "horse_no":  1,
                "sex":       "牡",
                "age":       4,
                "surface":   "芝",
                "distance":  1800,
                "bracket":   2,
                "race_date": "20250506",
                "odds":      5.0,
                "prev_race_time":     "1:34.5",
                "prev_race_distance": 1800,
                "horse_total_runs":   10,
                "horse_total_wins":   2,
                "prev_race_date":     "20250406",
            },
            {
                "race_id":   "202505060301",
                "horse_id":  "H002",
                "horse_no":  2,
                "sex":       "牝",
                "age":       3,
                "surface":   "芝",
                "distance":  1800,
                "bracket":   5,
                "race_date": "20250506",
                "odds":      10.0,
                "prev_race_time":     "1:35.0",
                "prev_race_distance": 1800,
                "horse_total_runs":   5,
                "horse_total_wins":   1,
                "prev_race_date":     "20250330",
            },
        ])

    def test_derived_columns_added(self):
        df = add_derived_features(self._make_minimal_df())
        expected_cols = [
            "venue_code", "n_horses", "cos_date", "sin_date",
            "implied_prob", "odds_is_missing",
            "prev_speed_index", "horse_win_rate",
        ]
        for col in expected_cols:
            assert col in df.columns, f"派生列 '{col}' が生成されていません"

    def test_row_count_preserved(self):
        df_in  = self._make_minimal_df()
        df_out = add_derived_features(df_in)
        assert len(df_out) == len(df_in), "行数が変化してはいけません"

    def test_deterministic(self):
        """同じ入力に対して2回呼んでも同じ結果が得られること"""
        df_in = self._make_minimal_df()
        df1   = add_derived_features(df_in.copy())
        df2   = add_derived_features(df_in.copy())
        numeric_cols = df1.select_dtypes(include="number").columns.tolist()
        pd.testing.assert_frame_equal(
            df1[numeric_cols].reset_index(drop=True),
            df2[numeric_cols].reset_index(drop=True),
            check_dtype=False,
        )

    def test_no_mutation_of_input(self):
        """元の DataFrame が変更されないこと（copy() の検証）"""
        df_in  = self._make_minimal_df()
        cols_before = set(df_in.columns)
        _      = add_derived_features(df_in)
        assert set(df_in.columns) == cols_before, "入力 DataFrame が変更されています"

    def test_implied_prob_norm_sums_to_one(self):
        df = add_derived_features(self._make_minimal_df())
        if "implied_prob_norm" in df.columns:
            total = df.groupby("race_id")["implied_prob_norm"].sum()
            for race_id, s in total.items():
                assert abs(s - 1.0) < 1e-6, f"race {race_id}: implied_prob_norm の合計が {s:.6f}"

    def test_n_horses_correct(self):
        df = add_derived_features(self._make_minimal_df())
        assert (df["n_horses"] == 2).all()
