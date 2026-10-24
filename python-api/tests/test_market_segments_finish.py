"""Synthetic-only checks for the immutable, format-only control adapter."""
import importlib.util
from pathlib import Path
import sys

import pandas as pd
import pytest


@pytest.fixture
def recovery(monkeypatch):
    # Importing this helper must not enter either the protected launcher or worker.
    monkeypatch.setattr(sys, "path", list(sys.path))
    path = Path(__file__).resolve().parents[2] / "scripts/run_market_edge_segments_finish.py"
    spec = importlib.util.spec_from_file_location("segment_finish_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def cohort():
    return pd.DataFrame({"race_id": ["202201010001", "202201010001", "202207010001", "202207010001"],
        "horse_number": [1, 2, 1, 2], "race_date": ["20220101", "20220101", "20220701", "20220701"],
        "phase": "validation", "finish": [1, 2, 2, 1], "odds": [2., 3., 4., 2.],
        "expected_runner_count": 2, "tansho_payout": [200., None, None, 200.]})


def save_control(recovery, tmp_path, cohort):
    path = tmp_path / "control.parquet"
    cohort.to_parquet(path, index=False)
    return path, recovery.sha256(path)


def control_for(cohort):
    return cohort[["race_id", "horse_number"]].assign(probability=[.61, .39, .28, .72])


def test_exact_verified_keys_preserve_probabilities_and_all_cohort_values(recovery, tmp_path, cohort):
    before = cohort.copy(deep=True)
    path, digest = save_control(recovery, tmp_path, control_for(cohort))
    result = recovery.fixed_control_with_dates(cohort, path, digest)
    pd.testing.assert_frame_equal(result[cohort.columns], before)
    assert result.probability.tolist() == [.61, .39, .28, .72]
    pd.testing.assert_frame_equal(cohort, before)
    assert recovery.sha256(path) == digest


def test_shuffled_rows_and_columns_join_by_keys_never_by_position(recovery, tmp_path, cohort):
    control = control_for(cohort).iloc[[3, 0, 2, 1]][["probability", "horse_number", "race_id"]]
    view = cohort.iloc[[2, 0, 3, 1]].copy()
    path, digest = save_control(recovery, tmp_path, control)
    result = recovery.fixed_control_with_dates(view, path, digest)
    expected = control_for(cohort).merge(cohort[["race_id", "horse_number", "race_date"]],
        on=["race_id", "horse_number"], validate="one_to_one")
    keys = ["race_id", "horse_number"]
    columns = keys + ["race_date", "probability"]
    pd.testing.assert_frame_equal(result[columns].sort_values(keys).reset_index(drop=True),
        expected[columns].sort_values(keys).reset_index(drop=True))
    assert result.race_date.tolist() == ["20220701", "20220101", "20220701", "20220101"]


def test_sha_mismatch_rejected_before_parquet_deserialization(recovery, tmp_path, cohort, monkeypatch):
    path, digest = save_control(recovery, tmp_path, control_for(cohort))
    changed = control_for(cohort)
    changed.loc[0, "probability"] = .62
    changed.to_parquet(path, index=False)
    monkeypatch.setattr(recovery.pd, "read_parquet", lambda *args, **kwargs: pytest.fail("Unverified control read"))
    with pytest.raises(ValueError, match="control changed"):
        recovery.fixed_control_with_dates(cohort, path, digest)


@pytest.mark.parametrize("malformation", ["missing", "extra", "duplicate", "wrong-horse", "wrong-race"])
def test_nonexact_control_runner_set_is_rejected(recovery, tmp_path, cohort, malformation):
    control = control_for(cohort)
    if malformation == "missing":
        control = control.iloc[:-1]
    elif malformation == "extra":
        control = pd.concat([control, control.iloc[:1].assign(horse_number=3)], ignore_index=True)
    elif malformation == "duplicate":
        control = pd.concat([control.iloc[:3], control.iloc[:1]], ignore_index=True)
    elif malformation == "wrong-horse":
        control.loc[0, "horse_number"] = 9
    else:
        control.loc[0, "race_id"] = "202201010099"
    path, digest = save_control(recovery, tmp_path, control)
    with pytest.raises(ValueError, match="runner set mismatch"):
        recovery.fixed_control_with_dates(cohort, path, digest)


def test_duplicate_keys_on_both_sides_do_not_bypass_one_to_one_guard(recovery, tmp_path, cohort):
    control = pd.concat([control_for(cohort), control_for(cohort).iloc[:1]], ignore_index=True)
    view = pd.concat([cohort, cohort.iloc[:1]], ignore_index=True)
    path, digest = save_control(recovery, tmp_path, control)
    with pytest.raises(pd.errors.MergeError, match="not a one-to-one merge"):
        recovery.fixed_control_with_dates(view, path, digest)


@pytest.mark.parametrize("malformation", ["extra-date", "extra-prediction", "missing-probability", "renamed-horse"])
def test_unexpected_schema_never_uses_generic_fallback(recovery, tmp_path, cohort, malformation):
    control = control_for(cohort)
    if malformation == "extra-date":
        control["race_date"] = "19990101"
    elif malformation == "extra-prediction":
        control["prediction"] = .5
    elif malformation == "missing-probability":
        control = control.drop(columns="probability")
    else:
        control = control.rename(columns={"horse_number": "horse"})
    path, digest = save_control(recovery, tmp_path, control)
    with pytest.raises(ValueError, match="Unexpected historical control schema"):
        recovery.fixed_control_with_dates(cohort, path, digest)
