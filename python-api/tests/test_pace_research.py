from importlib import import_module
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import pytest

sys.path[:0] = [str(Path(__file__).resolve().parents[1])]
from test_pace_forecast import population


def api():
    return import_module("training.pace_research")


def test_hierarchical_pace_compares_like_distances_and_freezes_thresholds():
    target, x = population(races=240)
    races = target.race_id.drop_duplicates().tolist()
    distance = {race: 1200 if i % 2 else 2000 for i, race in enumerate(races)}
    target["distance"] = target.race_id.map(distance).astype(float)
    laps = pd.DataFrame({"race_id": races, "validated": True, "source_sha256": "a" * 64})
    laps["first600_seconds"] = [33. + (i % 3) * 3 + (5 if distance[race] == 2000 else 0)
                                for i, race in enumerate(races)]
    labels, thresholds = api().hierarchical_labels(target, laps)
    assert labels.notna().all()
    assert set(labels.unique()) == {0., 1., 2.}
    for i, race in enumerate(races):
        assert labels.loc[target.race_id.eq(race)].iloc[0] == {0: 2., 1: 1., 2: 0.}[i % 3]
    changed = laps.assign(first600_seconds=55.)
    future_labels, _ = api().hierarchical_labels(target, changed, thresholds=thresholds)
    assert future_labels.eq(0).all()


def test_field_competition_retains_dispersion_lost_by_means():
    _, x = population(races=2)
    x.loc[:7, "ctx_front_rate5"] = [0., 0., 0., 0., 1., 1., 1., 1.]
    x.loc[8:, "ctx_front_rate5"] = .5
    target, _ = population(races=2)
    fields = api().field_features(target, x)
    assert fields.iloc[0].field_front_std > fields.iloc[8].field_front_std
    assert fields.iloc[0].field_front_candidates == 4
    assert fields.iloc[8].field_front_candidates == 8
    assert fields.groupby(target.race_id).nunique().le(1).all().all()


def test_race_nll_gradient_is_race_normalized_and_matches_finite_difference():
    raw = np.array([.1, -.3, 1., .2, -.8])
    truth = np.array([1., 0., 0., 1., 0.])
    dataset = lgb.Dataset(pd.DataFrame({"x": range(5)}), label=truth, group=[2, 3],
                          params={"verbosity": -1}, free_raw_data=False).construct()
    gradient, hessian = api().race_objective(raw, dataset)
    def loss(values):
        return sum(np.log(np.exp(values[sl]).sum()) - values[sl][truth[sl].argmax()]
                   for sl in (slice(0, 2), slice(2, 5)))
    for i in range(5):
        changed = np.zeros(5); changed[i] = 1e-5
        assert gradient[i] == pytest.approx((loss(raw + changed) - loss(raw - changed)) / 2e-5)
    assert abs(gradient[:2].sum()) < 1e-10
    assert abs(gradient[2:].sum()) < 1e-10
    assert np.all(hessian > 0)


def test_final_fit_calibrates_separately_and_rejects_outcome_features():
    parts = []
    for year in (2019, 2020, 2021):
        target, x = population(year, races=24)
        target["finish"] = target.horse_number
        target["horse_id"] = target.horse_number.map(str)
        target["time_seconds"] = 95. + target.horse_number
        target["race_id"] = target.race_id.astype("string")
        parts.extend((target, x))
    result = api().fit_final(*parts, objective="win", params={"num_boost_round": 12})
    assert np.isfinite(result["probabilities"]).all()
    assert pd.Series(result["probabilities"]).groupby(parts[4].race_id).sum().to_numpy() == pytest.approx(1.)
    assert result["calibration"]["fit_uses_test"] is False
    broken = list(parts); broken[1] = parts[1].assign(finish=1)
    with pytest.raises(ValueError, match="feature"):
        api().fit_final(*broken, objective="win")


def test_condition_control_keeps_actual_safe_condition_features():
    target, x = population(races=3)
    control = api().head_inputs(target, x, "condition")
    retained = [name for name in control if control[name].notna().any()]
    assert retained
    assert "safe_distance" in retained
    assert "safe_horse_number" not in retained


def test_budget_counts_actual_fits_and_never_resets():
    budget = api().FitBudget(maximum=2, deadline=100., clock=lambda: 50.)
    budget.reserve("a")
    budget.reserve("b")
    with pytest.raises(TimeoutError):
        budget.reserve("c")
    expired = api().FitBudget(maximum=20, deadline=40., clock=lambda: 50.)
    with pytest.raises(TimeoutError):
        expired.reserve("a")
