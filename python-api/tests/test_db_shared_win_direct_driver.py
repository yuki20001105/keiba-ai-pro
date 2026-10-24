"""Protocol, cohort and SELECT-before-TEST guards for the isolated win study."""
from copy import deepcopy
import importlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
DRIVER = importlib.import_module("evaluate_db_shared_win_direct")
WORKER = importlib.import_module("db_shared_win_direct_worker")
BETTING = importlib.import_module("db_shared_blend46_betting")


def protocol():
    return json.loads(DRIVER.CONFIG.read_text(encoding="utf-8"))


def frame():
    rows = []
    for day in ("20160105", "20200105", "20210105", "20220105", "20230105", "20240105", "20250105", "20260913"):
        for number in (1, 2, 3):
            rows.append({"race_id": day[:4] + "05010101", "race_date": day, "horse_number": number,
                "horse_id": "201300000" + str(number), "finish": number, "odds": (2., 5., 10.)[number - 1],
                "venue": "東京", "surface": "turf", "tansho_payout": 200. if number == 1 else np.nan,
                "expected_runner_count": 3, "probability": (0.625, 0.25, 0.125)[number - 1],
                "market_probability": (0.625, 0.25, 0.125)[number - 1],
                "temperature_probability": (0.625, 0.25, 0.125)[number - 1], "raw_score": 4. - number})
    return pd.DataFrame(rows)


def test_fixed_protocol_matches_worker_and_policy():
    DRIVER.validate_protocol(protocol())


@pytest.mark.parametrize("field,value", [("candidates", ["market46"]), ("feature_counts", [4, 50, 99]),
    ("main_policy_family_size", 18), ("bootstrap_repetitions", 10), ("maximum_parallel_fits", 4),
    ("threads_per_fit", 8), ("activation", True), ("forward_changes", True), ("actual_purchase", True)])
def test_protocol_tuning_or_mutation_rejected(field, value):
    changed = protocol()
    changed[field] = value
    with pytest.raises(ValueError, match="frozen-protocol"):
        DRIVER.validate_protocol(changed)


def test_calibration_formula_changes_rejected():
    changed = protocol()
    changed["calibration"]["ridge"] = .02
    with pytest.raises(ValueError, match="frozen-calibration"):
        DRIVER.validate_protocol(changed)


def feature_frame(base):
    value = base[["race_id", "race_date", "horse_number", "horse_id"]].copy()
    columns, categorical = WORKER.feature_contract("market46_extended")
    for name in columns[4:]:
        value[name] = pd.Series(["fixture"] * len(base), dtype="string") if name in categorical else np.nan
    return value


def test_feature_join_retains_all_runners_and_missing_values():
    base = frame()
    merged = DRIVER.merge_feature_cohort(base, feature_frame(base))
    assert len(merged) == len(base)
    assert merged.safe_prev_speed_mps.isna().all()
    assert merged.normalized_market_probability.between(0, 1).all()
    assert "probability" not in merged
    assert sum(len(x) for x in WORKER.split_positions(merged).values()) == len(base)


@pytest.mark.parametrize("kind", ["missing", "date", "horse", "duplicate"])
def test_feature_join_never_drops_partial_race_or_bad_identity(kind):
    base = frame()
    features = feature_frame(base)
    if kind == "missing":
        features = features.iloc[1:]
    elif kind == "date":
        features.loc[0, "race_date"] = "20160106"
    elif kind == "horse":
        features.loc[0, "horse_id"] = "wrong"
    else:
        features = pd.concat([features, features.iloc[:1]])
    with pytest.raises(ValueError):
        DRIVER.merge_feature_cohort(base, features)


def test_policy_freeze_happens_before_any_test_evaluation(tmp_path, monkeypatch):
    import db_shared_win_direct_diagnostics as diagnostics
    config = protocol()
    config["bootstrap_repetitions"] = 30  # fixture only, bypasses production protocol validation
    config["probability_bootstrap_repetitions"] = 30
    original = frame()
    split = {method: {phase: original.loc[original.race_date.between(*WORKER.PERIODS[phase])].reset_index(drop=True)
                     for phase in ("SELECT", "TEST")} for method in (*WORKER.CANDIDATES, "market_reciprocal")}
    events = []
    real_bootstrap = BETTING.bootstrap_policy_comparisons
    def check_test(part):
        if part.race_date.min() >= "20250101":
            assert (tmp_path / "frozen-selection.json").is_file()
            frozen = DRIVER.read(tmp_path / "frozen-selection.json")
            assert DRIVER.object_hash(frozen) == DRIVER.read(tmp_path / "frozen-selection-hash.json")["sha256"]
        events.append(part.race_date.min())
    def bootstrap(part, *args, **kwargs):
        check_test(part)
        return real_bootstrap(part, *args, **kwargs)
    def analyze(part, reference_frame=None, **kwargs):
        check_test(part)
        return {"nll": 1., "brier": .5, "no_fit": True}
    def selected(part, bets, **kwargs):
        check_test(part)
        return {"bets": len(bets), "no_fit": True}
    monkeypatch.setattr(BETTING, "bootstrap_policy_comparisons", bootstrap)
    monkeypatch.setattr(diagnostics, "analyze_probability", analyze)
    monkeypatch.setattr(diagnostics, "selection_calibration", selected)
    result = DRIVER.evaluate_predictions(split, config, tmp_path, lambda *args, **kwargs: None)
    assert events[:2] == ["20230105", "20250105"]
    assert len(result["policy_tables"]["TEST"]) == 19
    assert set(result["probability_metrics"]["TEST"]) == {*WORKER.CANDIDATES, "market_reciprocal"}
    assert all(result["frozen_selections"][name]["test_outcomes_consumed"] is False for name in WORKER.CANDIDATES)
    assert result["frozen_selections"] == DRIVER.read(tmp_path / "frozen-selection.json")


def test_result_load_rejects_missing_candidate():
    with pytest.raises(ValueError, match="all-three-candidates"):
        DRIVER.load_predictions([], ROOT / "not-used.parquet", "0" * 64, {})
