"""Isolated calibration fixtures; no historical artifact/application imports."""
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from prospective.calibration import (
    FAMILIES, evaluate_calibration, fit_calibrator, run_calibration_experiment,
    transform_probabilities,
)


def fixture(years=(2021,), races=6):
    rows = []
    for year in years:
        for race in range(races):
            winner = race % 3 + 1
            for number in (1, 2, 3):
                finish = 1 if number == winner else (2 if number == winner % 3 + 1 else 3)
                rows.append({"race_id": f"{year}R{race}", "race_date": f"{year}010{race % 8 + 1}",
                             "horse_id": str(number), "horse_number": number,
                             "surface": "turf" if race % 2 else "dirt", "expected_runner_count": 3,
                             "prediction": [0.8, 0.5, 0.2][number - 1], "finish": finish,
                             "fold_train_date_to": f"{year - 1}1231", "safe_prior_starts": number - 1,
                             "odds": 2.0 + number, "popularity": number, "tansho_payout": 300.0})
    return pd.DataFrame(rows)


def provenance():
    return {"model_id": "H04", "model_artifact_sha256_by_year": {str(year): "a" * 64 for year in range(2021, 2027)},
            "prediction_artifacts": [{"path": "verified-by-caller.parquet", "sha256": "b" * 64}]}


@pytest.mark.parametrize("family", FAMILIES)
def test_all_families_normalize_without_outcomes(family):
    frame = fixture()
    calibrated = fit_calibrator(frame, family, min_surface_races=2)
    predicted = transform_probabilities(frame.drop(columns=["finish", "fold_train_date_to", "odds"]), calibrated)
    assert np.allclose(predicted.groupby("race_id").win_probability.sum(), 1, rtol=0, atol=1e-15)
    assert predicted.win_probability.between(0, 1).all()
    assert "finish" not in predicted


def test_uniform_nll_and_brier_definitions():
    frame = fixture()
    probabilities = transform_probabilities(frame, fit_calibrator(frame, "uniform"))
    report = evaluate_calibration(probabilities)
    assert report["overall"]["race_nll"] == pytest.approx(np.log(3))
    assert report["overall"]["race_multiclass_brier"] == pytest.approx(2 / 3)
    assert report["overall"]["runner_brier"] == pytest.approx(2 / 9)
    assert report["overall"]["race_normalized_brier"] == pytest.approx(2 / 9)
    assert report["by_prior_starts"]["0-2"]["race_nll"] is None
    assert report["by_prior_starts"]["0-2"]["complete_races"] is False


def test_temperature_fit_improves_training_likelihood_and_stays_bounded():
    frame = fixture()
    frame["finish"] = frame.horse_number
    fitted = fit_calibrator(frame)
    assert 0.01 <= fitted["global"]["temperature"] <= 100
    first = evaluate_calibration(transform_probabilities(frame, fit_calibrator(frame, "softmax_t1")))
    second = evaluate_calibration(transform_probabilities(frame, fitted))
    assert second["overall"]["race_nll"] < first["overall"]["race_nll"]
    assert fitted["global"]["fit_status"] == "lower_temperature_boundary"


def test_race_constant_offsets_and_row_order_do_not_change_probabilities():
    frame = fixture()
    model = fit_calibrator(frame, "global_temperature")
    first = transform_probabilities(frame, model)
    frame.loc[frame.race_id.eq("2021R0"), "prediction"] += 100
    second = transform_probabilities(frame.sample(frac=1, random_state=1), model)
    np.testing.assert_allclose(first.win_probability, second.win_probability, atol=1e-14)


def test_constant_scores_use_identifiable_neutral_fallback():
    frame = fixture()
    frame["prediction"] = 100.0
    model = fit_calibrator(frame)
    assert model["global"]["temperature"] == 1.0
    assert model["global"]["fit_status"] == "constant_scores_no_identifiable_temperature"
    np.testing.assert_allclose(transform_probabilities(frame, model).win_probability, 1 / 3)


def test_surface_small_sample_and_unknown_surface_use_global():
    frame = fixture()
    model = fit_calibrator(frame, "surface_temperature", min_surface_races=4)
    assert model["by_surface"]["turf"]["fallback"] == "global"
    assert model["by_surface"]["dirt"]["fallback"] == "global"
    frame["surface"] = "unknown"
    global_model = fit_calibrator(fixture(), "global_temperature")
    np.testing.assert_allclose(transform_probabilities(frame, model).win_probability,
                               transform_probabilities(frame, global_model).win_probability)


def test_dead_heat_and_incomplete_outcomes_are_excluded_as_whole_races():
    frame = fixture()
    frame.loc[frame.race_id.eq("2021R0") & frame.horse_number.eq(2), "finish"] = 1
    frame.loc[frame.race_id.eq("2021R1") & frame.horse_number.eq(2), "finish"] = np.nan
    fitted = fit_calibrator(frame)
    audit = fitted["cohort_audit"]
    assert audit["excluded_dead_heat_races"] == 1
    assert audit["excluded_outcome_incomplete_races"] == 1
    assert audit["included_races"] == 4
    assert audit["excluded_runners"] == 6
    predicted = transform_probabilities(frame, fitted)
    assert len(predicted) == len(frame)
    assert evaluate_calibration(predicted)["cohort_audit"] == audit


@pytest.mark.parametrize("column,value,reason", [
    ("expected_runner_count", 4, "runner count"),
    ("prediction", np.inf, "Nonfinite"),
    ("fold_train_date_to", "20210101", "OOF"),
])
def test_invalid_input_is_rejected(column, value, reason):
    frame = fixture()
    frame.loc[0, column] = value
    with pytest.raises(ValueError, match=reason):
        fit_calibrator(frame)


def test_missing_oof_and_duplicate_runner_are_rejected():
    frame = fixture()
    with pytest.raises(ValueError, match="OOF"):
        fit_calibrator(frame.drop(columns="fold_train_date_to"))
    with pytest.raises(ValueError, match="Duplicate runner"):
        fit_calibrator(pd.concat([frame, frame.iloc[:1]]))


def test_unverified_or_non_normalized_probabilities_are_rejected():
    frame = fixture()
    frame["win_probability"] = 0.5
    with pytest.raises(ValueError, match="sum to one"):
        evaluate_calibration(frame)
    frame["win_probability"] = 1 / 3
    frame.loc[0, "win_probability"] = -1
    with pytest.raises(ValueError, match="Invalid win probability"):
        evaluate_calibration(frame)


def test_market_values_cannot_change_calibration():
    frame = fixture()
    changed = frame.assign(odds=100000, popularity=999, tansho_payout=-100)
    assert fit_calibrator(frame) == fit_calibrator(changed)


def test_probability_bins_include_one_and_empty_bins():
    frame = fixture()
    frame["finish"] = frame.horse_number
    frame["win_probability"] = frame.horse_number.eq(1).astype(float)
    metrics = evaluate_calibration(frame)["overall"]
    assert metrics["race_nll"] == 0
    assert metrics["runner_brier"] == 0
    assert metrics["reliability"]["ece"] == 0
    assert metrics["reliability"]["bins"][-1]["runner_count"] == 6


def test_frozen_envelope_detects_calibrator_tampering(tmp_path):
    from prospective.calibration import _sha
    model = fit_calibrator(fixture())
    frozen = {"calibrator": model, "calibrator_sha256": _sha(model)}
    transform_probabilities(fixture(), frozen)
    model["global"]["temperature"] = 1
    with pytest.raises(ValueError, match="hash mismatch"):
        transform_probabilities(fixture(), frozen)


def test_full_protocol_produces_immutable_json_and_only_later_probability_artifact(tmp_path):
    frame = fixture(tuple(range(2021, 2027)))
    output = tmp_path / "calibration"
    report = run_calibration_experiment(frame, output, model_provenance=provenance(), min_surface_races=2)
    assert set(report["selection_2022"]) == set(FAMILIES)
    assert report["protocol"]["roi_used_for_selection"] is False
    assert report["reselected_after_freeze"] is False
    frozen = json.loads((output / "frozen-calibrator.json").read_text())
    assert frozen["calibrator"]["fit_years"] == [2021, 2022]
    probabilities = pd.read_parquet(output / "diagnostic-probabilities.parquet")
    assert set(probabilities.race_date.str[:4]) == {"2023", "2024", "2025", "2026"}
    with pytest.raises(FileExistsError):
        run_calibration_experiment(frame, output, model_provenance=provenance())


def test_later_outcomes_and_scores_do_not_change_frozen_calibrator(tmp_path):
    frame = fixture(tuple(range(2021, 2027)))
    other = frame.copy()
    late = other.race_date.str[:4].astype(int).ge(2023)
    other.loc[late, "finish"] = other.loc[late, "horse_number"]
    other.loc[late, "prediction"] *= -3
    first, second = tmp_path / "first", tmp_path / "second"
    run_calibration_experiment(frame, first, model_provenance=provenance())
    run_calibration_experiment(other, second, model_provenance=provenance())
    assert (first / "frozen-calibrator.json").read_bytes() == (second / "frozen-calibrator.json").read_bytes()
    assert (first / "selection.json").read_bytes() == (second / "selection.json").read_bytes()


def test_later_bad_data_is_only_validated_after_freeze(tmp_path):
    frame = fixture(tuple(range(2021, 2027)))
    frame.loc[frame.race_date.str.startswith("2026"), "prediction"] = np.inf
    output = tmp_path / "frozen-before-error"
    with pytest.raises(ValueError, match="Nonfinite prediction"):
        run_calibration_experiment(frame, output, model_provenance=provenance())
    assert (output / "frozen-calibrator.json").exists()
    assert (output / "frozen-calibrator.sha256").exists()
    assert not (output / "calibration-report.json").exists()


def test_only_selection_2022_affects_family_selection(tmp_path):
    from prospective import calibration
    frame = fixture(tuple(range(2021, 2027)))
    original = calibration.fit_calibrator
    observed = []
    def observe(subset, *args, **kwargs):
        observed.append(set(subset.race_date.str[:4]))
        return original(subset, *args, **kwargs)
    with patch.object(calibration, "fit_calibrator", side_effect=observe):
        run_calibration_experiment(frame, tmp_path / "fit-audit", model_provenance=provenance())
    assert observed == [{"2021"}] * 4 + [{"2021", "2022"}]


def test_protocol_rejects_overlapping_years_and_missing_model_hash(tmp_path):
    frame = fixture(tuple(range(2021, 2027)))
    with pytest.raises(ValueError, match="chronology"):
        run_calibration_experiment(frame, tmp_path / "bad", model_provenance=provenance(), selection_years=(2021,))
    with pytest.raises(ValueError, match="SHA-256"):
        run_calibration_experiment(frame, tmp_path / "bad", model_provenance={"model_id": "H04"})
    assert not (tmp_path / "bad").exists()
