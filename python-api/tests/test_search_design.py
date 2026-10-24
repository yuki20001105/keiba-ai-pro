"""Finite search design and chronology, without real datasets or model fits."""
from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace

import pytest

from experiments import search, search_design as design
from experiments.common import read_json, write_json
from experiments.search_training import _trial_contract


def manifest():
    columns = [f"safe_{index:03d}" for index in range(101)]
    return {"feature_allowlist": columns, "parent_feature_sets": {"M03": columns[:72]}}


def result(identifier="R", *, rho=0.5, columns=None, target="ranking", annual=(0.49, 0.51), **metrics):
    return {"trial_id": identifier, "target": target, "validation_years": [2021, 2022],
            "feature_columns": columns or ["safe_a", "safe_b"],
            "evaluation": {"overall": {"rank_correlation": rho, "rmse": None,
                                        "rmse_speed_mps": None, **metrics},
                           "by_year": {str(year): {"rank_correlation": value}
                                       for year, value in zip((2021, 2022), annual)}}}


def effects(columns, *, gain=0.001):
    return [{"feature": name, "delta_rank": gain, "minimum_year_delta": gain}
            for name in columns]


def test_initial_hypotheses_have_exclusive_worker_compatible_loss_keys():
    specs = design.initial_candidates(manifest())
    assert len(specs) == 6
    assert len({s["id"] for s in specs}) == 6
    assert [len(s["feature_columns"]) for s in specs] == [72, 101, 101, 101, 72, 101]
    assert [_trial_contract(s)["params"]["objective"] for s in specs] == [
        "lambdarank", "lambdarank", "rank_xendcg", "huber", "regression", "regression"]
    for spec in specs:
        settings = _trial_contract(spec)
        assert ("ranking_loss" in spec) != ("regression_loss" in spec)
        assert settings["phase"] == "search"
        assert spec["validation_years"] == [2021, 2022]
        assert spec["bootstrap_samples"] == spec["contribution_sample"] == 0
        assert spec["save_model"] is False
        assert settings["trial"]["num_threads"] <= 4


def test_feature_removals_are_every_single_column_not_single_feature_models():
    base = design.initial_candidates(manifest())[2]
    before = deepcopy(base)
    drops = design.individual_candidates(base)
    assert len(drops) == 101
    assert [s["removed_features"][0] for s in drops] == base["feature_columns"]
    assert len({design.fingerprint(s) for s in drops}) == 101
    for spec in drops:
        assert len(spec["feature_columns"]) == 100
        assert spec["feature_columns"] == [c for c in base["feature_columns"] if c not in spec["removed_features"]]
        assert _trial_contract(spec)["params"]["objective"] == "rank_xendcg"
        assert spec["params"] == base["params"]
    assert base == before


@pytest.mark.parametrize("target,loss", [("ranking", "rank_xendcg"), ("rank_regression", "huber"), ("regression", "mae")])
def test_conditions_and_refinement_preserve_the_selected_model_family(target, loss):
    key = "ranking_loss" if target == "ranking" else "regression_loss"
    base = design.specification("selected", manifest()["feature_allowlist"][3:], target=target, **{key: loss})
    regression = design.specification("speed", manifest()["feature_allowlist"][:72], target="regression")
    candidates = design.condition_candidates(base, regression)
    assert len(candidates) == design.PLAN["maximum_conditions"] == 18
    assert {s["train_start_year"] for s in candidates} == {2016, 2018, 2020}
    assert {s["regression_loss"] for s in candidates[12:]} == {"mse", "mae", "huber"}
    for spec in candidates:
        settings = _trial_contract(spec)
        assert settings["rounds"] <= 1000
        assert spec["validation_years"] == [2021, 2022]
    selected = candidates[6]
    refinements = design.individual_candidates(selected, limit=16, prefix="R")
    assert len(refinements) == 16
    for spec in refinements:
        assert spec["target"] == target and spec[key] == loss
        assert spec["train_start_year"] == 2018
        assert spec["params"] == selected["params"]
        assert len(spec["feature_columns"]) == len(selected["feature_columns"]) - 1
        _trial_contract(spec)


@pytest.mark.parametrize("gain", [0.001, -0.1])
def test_combinations_are_bounded_unique_nonempty_removals_including_fallback(gain):
    base = design.initial_candidates(manifest())[1]
    combined = design.combination_candidates(base, effects(base["feature_columns"], gain=gain), prefix="C", maximum=24)
    assert 1 < len(combined) <= 24
    assert len({tuple(s["feature_columns"]) for s in combined}) == len(combined)
    for spec in combined:
        assert spec["feature_columns"]
        assert 2 <= len(spec["removed_features"]) <= 8
        assert set(spec["feature_columns"]) == set(base["feature_columns"]) - set(spec["removed_features"])
        _trial_contract(spec)
    assert len(design.combination_candidates(base, effects(base["feature_columns"]), prefix="RC", maximum=12)) == 12


def test_full_contracts_require_full_evaluation_without_changing_model_spec():
    base = design.condition_candidates(design.initial_candidates(manifest())[3], design.initial_candidates(manifest())[5])[7]
    for years, phase in [([2021, 2022], "search"), ([2023, 2024, 2025], "confirmation"), ([2026], "retrospective")]:
        full = design.full_spec(base, f"fixed-{phase}", years)
        settings = _trial_contract(full)
        assert settings["phase"] == phase
        assert full["bootstrap_samples"] == 300 and full["contribution_sample"] == 128
        assert full["save_model"] is True
        for key in ("target", "regression_loss", "params", "feature_columns", "train_start_year", "seed"):
            assert full[key] == base[key]


def test_fingerprint_ignores_only_descriptions_and_not_semantic_settings():
    base = design.initial_candidates(manifest())[1]
    assert design.fingerprint(base) == design.fingerprint({**base, "id": "renamed", "description": "note", "removed_features": []})
    for change in ({"seed": 7}, {"train_start_year": 2018}, {"ranking_loss": "rank_xendcg"},
                   {"feature_columns": base["feature_columns"][:-1]}, {"params": {**base["params"], "num_leaves": 15}}):
        assert design.fingerprint(base) != design.fingerprint({**base, **change})


def test_accuracy_selection_uses_neither_roi_win_rate_nor_cross_target_rmse():
    a = result("better-rank", rho=0.51, win_roi_percent=-900, top1_win_rate=0, rmse=999)
    b = result("better-betting", rho=0.50, win_roi_percent=10000, top1_win_rate=1, rmse=0)
    assert design.best_accuracy([a, b]) is a
    regression = result("regression", target="regression", rmse=999, rmse_speed_mps=99)
    ranking = result("ranking")
    assert design.best_accuracy([regression, ranking]) is regression
    assert design.best_accuracy([ranking, regression]) is ranking


def test_accuracy_tie_break_is_fewer_features_then_worst_annual_rank():
    best = result("max", rho=0.5000005, columns=["a", "b", "c"], annual=(0.5, 0.5))
    smaller = result("small", rho=0.5, columns=["a", "b"], annual=(0.1, 0.9))
    stable = result("stable", rho=0.5, columns=["a", "b"], annual=(0.4, 0.6))
    too_low = result("outside-tolerance", rho=0.499, columns=["a"], annual=(0.499, 0.499))
    assert design.best_accuracy([best, smaller, stable, too_low]) is stable


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), "0.5"])
def test_accuracy_selection_rejects_nonfinite_or_missing_metrics(bad):
    overall = result()
    overall["evaluation"]["overall"]["rank_correlation"] = bad
    with pytest.raises(ValueError, match="missing-rank"):
        design.best_accuracy([overall])
    annual = result()
    annual["evaluation"]["by_year"]["2022"]["rank_correlation"] = bad
    with pytest.raises(ValueError, match="missing-annual"):
        design.best_accuracy([annual])


@pytest.mark.parametrize("years", [[2023, 2024, 2025], [2026], [2021], [2021, 2022, 2023]])
def test_accuracy_selection_rejects_non_search_evaluation_windows(years):
    candidate = result()
    candidate["validation_years"] = years
    with pytest.raises(ValueError, match="only-search-years"):
        design.best_accuracy([candidate])


def test_accuracy_selection_requires_both_annual_search_metrics():
    candidate = result()
    del candidate["evaluation"]["by_year"]["2022"]
    with pytest.raises(ValueError, match="missing-annual"):
        design.best_accuracy([candidate])


def test_complete_mock_campaign_freezes_before_confirmation_and_stays_below_cap(tmp_path, monkeypatch):
    """Exercise actual run ordering; all fits, stores and external work are fake."""
    from experiments import betting_search

    root = tmp_path / "synthetic-search"
    root.mkdir()
    stages = []
    observed = {}
    finished = []
    frozen_winner = []

    class FakeCampaign(search.Campaign):
        def execute(self, name, payloads):
            stages.append((name, deepcopy(payloads)))
            if name == "08-frozen-full-verification":
                frozen = read_json(root / "selection.json")
                assert frozen["frozen_before_confirmation"] is True
                assert frozen["winner_spec"]["validation_years"] == [2021, 2022]
                assert frozen["winner_spec"]["seed"] == 42
                frozen_winner.append(frozen["winner_trial"])
            self.specs.update({s["id"]: s for s in payloads})
            stage_results = {}
            for spec in payloads:
                _trial_contract(spec)
                rho = (0.45 if spec["target"] == "regression" else
                       0.49 if spec["id"] == "B-rank-72" else 0.5)
                if spec["id"] == "H07" or spec["id"].startswith("R"):
                    rho = 0.52
                # Alternate seeds and later windows are deliberately superior:
                # neither may alter the frozen selected search model.
                if spec["seed"] != 42 or spec["validation_years"] != [2021, 2022]:
                    rho = 0.999
                candidate = result(spec["id"], rho=rho, columns=spec["feature_columns"], target=spec["target"],
                                   annual=(rho, rho), rmse=10 if spec["target"] == "regression" else None,
                                   rmse_speed_mps=0.4 if spec["target"] == "regression" else None)
                if spec["id"] == "B-speed-72":
                    # Normalized and physical RMSE deliberately disagree.
                    candidate["evaluation"]["overall"].update(rmse=0.01, rmse_speed_mps=0.8)
                candidate["validation_years"] = spec["validation_years"]
                stage_results[spec["id"]] = candidate
                write_json(root / "trials" / spec["id"] / "result.json", candidate)
            self.results.update(stage_results)
            observed.update(stage_results)
            return stage_results

    monkeypatch.setattr(search, "BASE", tmp_path)
    monkeypatch.setattr(search, "driver_lock", lambda *args: nullcontext())
    monkeypatch.setattr(search, "_initialize", lambda _: (manifest(), {}))
    monkeypatch.setattr(search, "store_for", lambda _: SimpleNamespace(snapshot=lambda _: {"status": "active"},
                                                                     finish_experiment=lambda identifier: finished.append(identifier)))
    monkeypatch.setattr(search, "Campaign", FakeCampaign)
    monkeypatch.setattr(search, "_frozen_predictions", lambda *args: ("fake-frame", [{"mock": True}]))
    monkeypatch.setattr(search, "verify_contract", lambda *args: None)

    def fake_betting(frame, output, **kwargs):
        assert frame == "fake-frame"
        assert kwargs["model_id"] == frozen_winner[0]
        assert kwargs["prediction_provenance"]["model_fixed_before_rules"] is True
        assert read_json(root / "selection.json")["winner_trial"] == frozen_winner[0]
        return {"mock": True}

    monkeypatch.setattr(betting_search, "evaluate_betting_search", fake_betting)
    monkeypatch.setattr(search, "write_report", lambda root, contract, campaign, selection, betting, elapsed: selection)
    selected = search.run(root)
    assert len(stages) == 8
    assert len(stages[1][1]) == 101
    assert len(stages[3][1]) == 18
    assert len(stages[4][1]) == 16
    assert len(stages[-1][1]) == 9
    assert sum(len(payloads) for _, payloads in stages) <= 188 <= design.PLAN["max_model_trials"]
    ids = [spec["id"] for _, payloads in stages for spec in payloads]
    assert len(set(ids)) == len(ids)
    assert set(selected["result_hashes"]) == set(ids) - {s["id"] for s in stages[-1][1]}
    assert selected["winner_trial"] == frozen_winner[0]
    assert not selected["winner_trial"].startswith("seed-")
    assert selected["winner_spec"]["seed"] == 42
    assert selected["winner_spec"]["train_start_year"] == 2018
    assert selected["winner_trial"].startswith("RC")
    assert "B-speed-72" not in selected["regression_pareto"]
    assert "B-speed-all" in selected["regression_pareto"]
    for spec in stages[4][1] + stages[5][1]:
        assert spec["train_start_year"] == 2018
        assert spec["params"]["num_boost_round"] == 400
    assert finished == [root.name]
    assert selected["rule"]["roi_or_top1_used_for_model_selection"] is False
    assert selected["rule"]["all_confirmation_periods_known_reused"] is True
