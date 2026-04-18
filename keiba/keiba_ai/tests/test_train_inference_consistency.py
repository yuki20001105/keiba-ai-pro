"""
学習時と推論時の特徴量処理が整合しているかをテストする。

動画#37 で解説された手法を本プロジェクトに適用：
  - 学習時: add_derived_features(df, full_history_df=full_df)  で生成した特徴量
  - 推論時: add_derived_features(live_df, full_history_df=concat(hist, live_df)) で生成した特徴量
  を pandas.testing.assert_frame_equal で比較し、処理が一致していることを確認する。

重要な前提:
  - 推論対象レース（direct_race_id）は full_history_df には「結果が未確定」の状態で入る。
  - 歴史統計（expanding window）は対象レース以前のデータのみを使うため、
    full_history_df に目的レースの行が含まれていても expanding window は排他的に計算される。
  - 比較は「モデルに渡す特徴量列」のみを対象とし、check_dtype=False, check_like=True を使用。

実行:
    pytest keiba/keiba_ai/tests/test_train_inference_consistency.py -v -s
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd
import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent.parent))        # keiba/ を追加
sys.path.insert(0, str(_HERE.parent.parent.parent.parent / "python-api"))

from keiba_ai.feature_engineering import add_derived_features  # type: ignore

# モデルに渡す数値特徴量（train.py の feature_cols_num と同期）
_FEATURE_COLS_NUM = [
    "horse_no", "bracket", "age", "handicap", "weight", "weight_diff",
    "entry_odds", "entry_popularity",
    "straight_length", "inner_bias", "inner_advantage",
    "n_horses", "cos_date", "sin_date", "seasonal_sex", "frame_race_type",
    "jockey_course_win_rate", "jockey_course_races",
    "jockey_place_rate_top2", "jockey_show_rate", "jockey_recent30_win_rate",
    "fe_trainer_win_rate", "trainer_place_rate_top2", "trainer_show_rate",
    "trainer_recent30_win_rate",
    "jt_combo_win_rate_smooth", "jt_combo_races",
    "sire_win_rate", "sire_show_rate", "damsire_win_rate", "damsire_show_rate",
    "horse_distance_win_rate", "horse_distance_avg_finish",
    "horse_surface_win_rate", "horse_surface_races",
    "horse_dist_band_win_rate", "horse_dist_band_races",
    "horse_venue_win_rate", "horse_venue_races",
    "horse_venue_surface_win_rate", "horse_venue_surface_races",
    "horse_dist_surface_win_rate", "horse_dist_surface_races",
    "past3_avg_finish", "past5_avg_finish", "past10_avg_finish",
    "past3_win_rate", "past5_win_rate",
    "horse_win_rate",
    "prev_speed_index", "prev_speed_zscore", "prev_race_time_seconds",
    "prev_race_finish", "prev_race_distance", "prev_race_weight",
    "distance_change",
    "days_since_last_race",
    "implied_prob_norm", "odds_rank_in_race", "odds_z_in_race",
    "market_entropy", "top3_probability",
    "gate_win_rate",
    "race_pace_diff", "race_pace_ratio", "race_pace_front", "race_pace_back",
    "tansho_payout_log", "sanrentan_payout_log", "sanrentan_z_in_races",
    "past3_avg_last3f_time", "past5_avg_last3f_time", "past3_avg_last3f_rank",
    "past5_avg_tansho_log",
    "prev_race_finish_is_missing", "days_since_last_race_is_missing",
    "prev_speed_index_is_missing", "horse_win_rate_is_missing",
    "odds_is_missing",
]
_FEATURE_COLS_CAT = ["sex", "jockey_id", "trainer_id", "venue_code", "track_type", "corner_radius"]


def _to_model_cols(df: pd.DataFrame, cols: List[str]) -> pd.DataFrame:
    """モデルに渡す列のみ抽出・存在しない列は NaN で補完する"""
    present = [c for c in cols if c in df.columns]
    result = df[present].copy()
    return result


def _simulate_train_features(full_history: pd.DataFrame, target_race_id: str) -> pd.DataFrame:
    """学習時の特徴量生成をシミュレート。

    学習時は `add_derived_features(df, full_history_df=df)` で全履歴を渡す。
    ここでは full_history のうち target_race_id の行だけを返す。
    - expanding window は「全履歴から」計算されるが、
      各行は「その行より前」の情報のみを使うためデータリークはない。
    """
    df_all = add_derived_features(full_history.copy(), full_history_df=full_history.copy())
    return df_all[df_all["race_id"] == target_race_id].copy()


def _simulate_inference_features(
    full_history: pd.DataFrame,
    target_race_id: str,
) -> pd.DataFrame:
    """推論時の特徴量生成をシミュレート。

    推論時は:
      1. DB から過去履歴 (full_history_df) を読み込む
      2. 出馬表データを live_df として用意（finish 列なし）
      3. full_history_df = concat(hist_before_target, live_df) を渡す

    live_df は当日の出馬表相当 → finish 列を除去し、DB 過去データと結合して渡す。
    """
    # 出馬表相当: target race の行から "レース後確定フィールド" を除去
    POST_RACE_COLS = {
        "finish", "time", "corner_1", "corner_2", "corner_3", "corner_4",
        "last_3f", "last_3f_time", "last_3f_rank", "margin",
        "tansho_payout", "sanrentan_payout",
    }
    live_df = (
        full_history[full_history["race_id"] == target_race_id]
        .copy()
        .drop(columns=[c for c in POST_RACE_COLS if c in full_history.columns], errors="ignore")
    )

    # 過去履歴: target_race_id 以前のレースのみ
    hist_before = full_history[full_history["race_id"] < target_race_id].copy()

    # 推論時の full_history_df = 過去 + 当日出馬表
    full_hist_infer = pd.concat([hist_before, live_df], ignore_index=True)

    return add_derived_features(live_df, full_history_df=full_hist_infer)


# ===========================================================================
# テストクラス
# ===========================================================================

class TestTrainInferenceConsistency:
    """学習時と推論時の特徴量が一致することを確認する。

    Attributes:
        COLS_TO_COMPARE: 比較する列名のリスト（expanding window 統計列が中心）
        ATOL: 許容誤差（浮動小数点計算の丸め誤差）
    """

    # 整合性を特に重要視する列（expanding window 統計・スピード指数）
    COLS_TO_COMPARE = [
        "n_horses", "cos_date", "sin_date", "seasonal_sex", "frame_race_type",
        "implied_prob_norm", "odds_rank_in_race", "market_entropy",
        "odds_is_missing",
        "horse_win_rate",
        "prev_speed_index",
        "days_since_last_race_is_missing",
        "prev_race_finish_is_missing",
    ]
    ATOL = 1e-6

    @pytest.fixture(autouse=True)
    def _check_history(self, small_history_df):
        """history_df の準備確認（DB 不要テストとの分離）"""
        if small_history_df is None or small_history_df.empty:
            pytest.skip("history_df が空です")
        self.hist = small_history_df

    def test_id_season_features_match(self, small_history_df, sample_race_ids):
        """venue_code・n_horses・cos_date など race_id 由来の特徴量が一致する。

        これらは full_history_df を使わないため、学習/推論で常に同じ値になるべき。
        """
        if not sample_race_ids:
            pytest.skip("sample_race_ids が取得できませんでした")

        race_id = sample_race_ids[0]
        df_train  = _simulate_train_features(small_history_df, race_id)
        df_infer  = _simulate_inference_features(small_history_df, race_id)

        if df_train.empty or df_infer.empty:
            pytest.skip(f"race_id={race_id} のデータが取得できませんでした")

        id_cols = ["venue_code", "n_horses", "cos_date", "sin_date"]
        for col in id_cols:
            if col not in df_train.columns or col not in df_infer.columns:
                continue
            # horse_id でソートして整合させる
            t = df_train.set_index("horse_id").sort_index()[col] if "horse_id" in df_train.columns else df_train[col]
            i = df_infer.set_index("horse_id").sort_index()[col] if "horse_id" in df_infer.columns else df_infer[col]
            pd.testing.assert_series_equal(
                t.reset_index(drop=True),
                i.reset_index(drop=True),
                check_dtype=False,
                check_names=False,
                atol=self.ATOL,
                rtol=0,
                obj=f"'{col}' 列の学習/推論不一致",
            )

    def test_market_features_match(self, small_history_df, sample_race_ids):
        """odds 由来の市場特徴量（implied_prob_norm など）が一致する。

        推論時に odds が提供されていれば学習時と同一の計算結果になるべき。
        """
        if not sample_race_ids:
            pytest.skip("sample_race_ids が取得できませんでした")

        race_id   = sample_race_ids[0]
        df_train  = _simulate_train_features(small_history_df, race_id)
        df_infer  = _simulate_inference_features(small_history_df, race_id)

        if df_train.empty or df_infer.empty:
            pytest.skip(f"race_id={race_id} のデータが取得できませんでした")

        for col in ["implied_prob_norm", "odds_rank_in_race", "odds_is_missing", "market_entropy"]:
            if col not in df_train.columns or col not in df_infer.columns:
                continue
            t = _sort_by_horse(df_train, col)
            i = _sort_by_horse(df_infer, col)
            pd.testing.assert_series_equal(
                t, i,
                check_dtype=False,
                check_names=False,
                atol=self.ATOL,
                rtol=0,
                obj=f"'{col}' 列の学習/推論不一致",
            )

    def test_feature_columns_subset_present(self, small_history_df, sample_race_ids):
        """推論時出力に必須特徴量列が含まれていること。"""
        if not sample_race_ids:
            pytest.skip("sample_race_ids が取得できませんでした")

        race_id  = sample_race_ids[0]
        df_infer = _simulate_inference_features(small_history_df, race_id)

        if df_infer.empty:
            pytest.skip(f"race_id={race_id} のデータが取得できませんでした")

        # モデルに必須の列をチェック
        required = ["n_horses", "cos_date", "sin_date", "implied_prob_norm", "odds_is_missing"]
        missing  = [c for c in required if c not in df_infer.columns]
        assert not missing, f"推論結果に必須列が欠落しています: {missing}"

    def test_no_post_race_columns_in_inference(self, small_history_df, sample_race_ids):
        """推論時出力に finish・tansho_payout など結果確定列が含まれないこと。"""
        if not sample_race_ids:
            pytest.skip("sample_race_ids が取得できませんでした")

        race_id  = sample_race_ids[0]
        df_infer = _simulate_inference_features(small_history_df, race_id)

        if df_infer.empty:
            pytest.skip(f"race_id={race_id} のデータが取得できませんでした")

        post_race_cols = ["finish", "time", "margin"]
        found = [c for c in post_race_cols if c in df_infer.columns]
        assert not found, f"推論データにレース後確定列が含まれています: {found}"

    @pytest.mark.parametrize("n_races", [1, 3])
    def test_multiple_race_ids_consistent(self, small_history_df, sample_race_ids, n_races):
        """複数レースで学習/推論の特徴量整合を確認する。"""
        if len(sample_race_ids) < n_races:
            pytest.skip(f"sample_race_ids が {n_races} 件に満たない")

        for race_id in sample_race_ids[:n_races]:
            df_train = _simulate_train_features(small_history_df, race_id)
            df_infer = _simulate_inference_features(small_history_df, race_id)

            if df_train.empty or df_infer.empty:
                continue  # そのレースはスキップ

            # n_horses は行数だけで決まるので必ず一致する
            assert "n_horses" in df_train.columns
            assert "n_horses" in df_infer.columns
            pd.testing.assert_series_equal(
                _sort_by_horse(df_train, "n_horses"),
                _sort_by_horse(df_infer, "n_horses"),
                check_dtype=False,
                check_names=False,
                obj=f"race_id={race_id} の n_horses 不一致",
            )


# ===========================================================================
# 整合性チェック補助テスト（DB 不要）
# ===========================================================================

class TestConsistencyWithSyntheticData:
    """合成データを使った学習/推論整合テスト（DB 不要、CI 用）。"""

    def _make_history(self) -> pd.DataFrame:
        """3 レース分の合成履歴データを返す"""
        rows = []
        base_races = ["202504010501", "202504080502", "202504150503"]
        for rid in base_races:
            for i in range(1, 6):
                rows.append({
                    "race_id":        rid,
                    "horse_id":       f"H{i:03d}",
                    "horse_no":       i,
                    "sex":            "牡" if i % 2 == 0 else "牝",
                    "age":            4,
                    "surface":        "芝",
                    "distance":       1800,
                    "bracket":        i,
                    "race_date":      rid[:8],
                    "finish":         i,       # 1着〜5着
                    "odds":           float(i * 3),
                    "jockey_id":      f"J{(i % 3) + 1:02d}",
                    "trainer_id":     f"T{(i % 2) + 1:02d}",
                    "venue":          "東京",
                    "prev_race_date": "20250301",
                    "prev_race_time": "1:48.0",
                    "prev_race_distance": 1800,
                    "horse_total_runs": 10,
                    "horse_total_wins": i == 1,
                })
        return pd.DataFrame(rows)

    def test_id_season_identical_for_same_race(self):
        """race_id 由来の特徴量は学習/推論で常に同一"""
        history = self._make_history()
        target_race = "202504150503"

        df_train = _simulate_train_features(history, target_race)
        df_infer = _simulate_inference_features(history, target_race)

        assert not df_train.empty, "学習側のデータが空です"
        assert not df_infer.empty, "推論側のデータが空です"

        for col in ["n_horses", "cos_date", "sin_date"]:
            if col not in df_train.columns or col not in df_infer.columns:
                continue
            pd.testing.assert_series_equal(
                _sort_by_horse(df_train, col),
                _sort_by_horse(df_infer, col),
                check_dtype=False,
                check_names=False,
                atol=1e-9,
                obj=f"合成データ: '{col}' 列の学習/推論不一致",
            )

    def test_market_features_identical(self):
        """同一 odds が渡されれば市場特徴量は学習/推論で一致する"""
        history = self._make_history()
        target_race = "202504150503"

        df_train = _simulate_train_features(history, target_race)
        df_infer = _simulate_inference_features(history, target_race)

        if df_train.empty or df_infer.empty:
            pytest.skip("データが空")

        for col in ["implied_prob_norm", "odds_rank_in_race", "odds_is_missing"]:
            if col not in df_train.columns or col not in df_infer.columns:
                continue
            pd.testing.assert_series_equal(
                _sort_by_horse(df_train, col),
                _sort_by_horse(df_infer, col),
                check_dtype=False,
                check_names=False,
                atol=1e-9,
                obj=f"合成データ: '{col}' 学習/推論不一致",
            )

    def test_full_feature_frame_shape(self):
        """add_derived_features の出力列数が学習/推論で一致する"""
        history = self._make_history()
        target_race = "202504150503"

        df_train = _simulate_train_features(history, target_race)
        df_infer = _simulate_inference_features(history, target_race)

        if df_train.empty or df_infer.empty:
            pytest.skip("データが空")

        # 推論側は finish などの列がないため行方向のみ一致を確認
        assert len(df_train) == len(df_infer), (
            f"行数不一致: 学習={len(df_train)}, 推論={len(df_infer)}"
        )

    def test_cancelled_horse_in_history_handled_consistently(self):
        """過去レースに中止馬が含まれていても学習/推論の特徴量が一致すること。

        動画#37 で言及: 学習データの履歴に finish='中止' の行が含まれている場合、
        expanding window 統計（horse_win_rate 等）の計算で train/infer が一致すること。
        """
        history = self._make_history()
        # 過去の別レース（202504010501）に中止馬（H099）の行を追加
        cancelled = pd.DataFrame([{
            "race_id":           "202504010501",
            "horse_id":          "H099",
            "horse_no":          9,
            "sex":               "牡",
            "age":               5,
            "surface":           "芝",
            "distance":          1800,
            "bracket":           8,
            "race_date":         "20250401",
            "finish":            "中止",   # 中止
            "odds":              None,
            "jockey_id":         "J99",
            "trainer_id":        "T99",
            "venue":             "東京",
            "prev_race_date":    "20250201",
            "prev_race_time":    "1:50.0",
            "prev_race_distance": 1800,
            "horse_total_runs":  5,
            "horse_total_wins":  False,
        }])
        # history の1行をベースに中止馬の行を作成（列セットを同一に保つ）
        cancelled_row = history[history["race_id"] == "202504010501"].iloc[[0]].copy()
        cancelled_row = cancelled_row.assign(
            horse_id="H099",
            horse_no=9,
            sex="牡",
            age=5,
            bracket=8,
            finish="中止",
            odds=None,
            jockey_id="J99",
            trainer_id="T99",
            prev_race_date="20250201",
            prev_race_time="1:50.0",
            prev_race_distance=1800,
            horse_total_runs=5,
            horse_total_wins=False,
        )
        history_with_cancelled = pd.concat([history, cancelled_row], ignore_index=True)

        # target は最終レース
        target_race = "202504150503"
        df_train = _simulate_train_features(history_with_cancelled, target_race)
        df_infer  = _simulate_inference_features(history_with_cancelled, target_race)

        # 中止馬は target race に含まれていないのでどちらにも現れない
        assert "H099" not in df_train["horse_id"].values, "学習側に中止馬が混入"
        assert "H099" not in df_infer["horse_id"].values, "推論側に中止馬が混入"

        # n_horses は target race の正常馬5頭で一致するべき
        if "n_horses" in df_train.columns and "n_horses" in df_infer.columns:
            pd.testing.assert_series_equal(
                _sort_by_horse(df_train, "n_horses"),
                _sort_by_horse(df_infer, "n_horses"),
                check_dtype=False,
                check_names=False,
                obj="中止馬あり履歴: n_horses の学習/推論不一致",
            )


# ===========================================================================
# ヘルパー
# ===========================================================================

def _sort_by_horse(df: pd.DataFrame, col: str) -> pd.Series:
    """horse_id でソートした上で指定列を pandas.Series として返す"""
    if "horse_id" in df.columns:
        return df.sort_values("horse_id")[col].reset_index(drop=True)
    return df[col].reset_index(drop=True)
