"""Read-only review cannot repair or overwrite the numerical study."""
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import review_db_shared_win_four_artifacts as review


def worker_files(tmp_path):
    study = tmp_path / "study"
    study.mkdir()
    job = "2016-expanding_binary"
    request = {"job_id": job, "candidate_id": "expanding_binary", "fold_year": 2016,
        "frame_sha256": "x", "protocol_sha256": "y", "phase1_receipt_sha256": "z",
        "feature_columns": list(range(50)), "categorical_columns": [], "expected_source_hashes": {}}
    artifacts = {}
    for name in ("model", "calibration_predictions", "predictions"):
        path = study / (name + ".bin")
        path.write_bytes(name.encode())
        artifacts[name] = {"path": str(path), "sha256": review.digest(path)}
    bounds = {name: list(values) for name, values in review.expected_periods(2016, "expanding_binary").items()}
    result = {**request, "schema": "db-shared-win-four-worker-v1", "status": "completed",
        "request_sha256": review.hashlib.sha256(review.canonical(request).encode()).hexdigest(),
        "source_hashes": {}, "feature_count": 50, "periods": bounds,
        "runtime": {"device": "cpu", "native_threads": 2, "rounds": 150},
        "partitions": {name: {"date_from": lower, "date_to": upper} for name, (lower, upper) in bounds.items()},
        "artifacts": artifacts, "reload_parity": {"CAL": {"probability": 0.0}, "TEST": {"probability": 0.0}},
        "calibration_audit": {"converged": True, "at_bound": False}}
    (study / (job + "-request.json")).write_text(review.canonical(request), encoding="utf-8")
    (study / (job + ".json")).write_text(review.canonical(result), encoding="utf-8")
    return study, result


def test_period_representation_only_and_old_files_untouched(tmp_path):
    study, result = worker_files(tmp_path)
    before = review.fingerprint(study)
    receipt = review.review_worker(study, result)
    assert receipt["native_equality"] is False
    assert receipt["normalized_period_equality"] is True
    assert receipt["other_validator_bindings_match"] is True
    assert len(receipt["artifact_hashes_verified"]) == 3
    assert review.fingerprint(study) == before


def test_normalization_does_not_accept_changed_date_value(tmp_path):
    study, result = worker_files(tmp_path)
    result["periods"]["TEST"][0] = "20160102"
    with pytest.raises(ValueError, match="period_values"):
        review.review_worker(study, result)


def test_unchanged_types_cannot_hide_tampered_artifact(tmp_path):
    study, result = worker_files(tmp_path)
    Path(result["artifacts"]["predictions"]["path"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="artifact_hash:predictions"):
        review.review_worker(study, result)


def test_bad_binding_not_accepted_as_representation_only(tmp_path):
    study, result = worker_files(tmp_path)
    result["feature_count"] = 49
    with pytest.raises(ValueError, match="result_binding:feature_count"):
        review.review_worker(study, result)


def test_fixed_periods_early_rolling_equals_expanding_but_later_differs():
    for year in range(2016, 2020):
        assert review.expected_periods(year, "rolling5_binary") == review.expected_periods(year, "expanding_binary")
    assert review.expected_periods(2026, "rolling5_race_nll")["TRAIN"] == ("20200101", "20241231")
    assert review.expected_periods(2026, "expanding_race_nll")["TRAIN"] == ("20130101", "20241231")


def fake_full_metrics():
    import copy
    methods = ("market_reference", *review.CANDIDATES)
    policy_ids = ["market_favorite"] + [candidate + ":" + rule
        for candidate in review.CANDIDATES for rule in ("top1", "top1_ev105")]
    metric = {"race_count": 10, "runner_count": 100, "date_from": "20250101", "date_to": "20260913",
        "nll": 2.0, "brier": .8, "spearman": .5, "top1": .3, "rmse_win_indicator": .08 ** .5,
        "physical_speed_rmse": None, "comparison_vs_reference": None, "odds_band_calibration": []}
    calibration = {"selected_bet_count": 10, "selected_race_count": 10, "selection_race_coverage": 1.,
        "interval_scope": "secondary-exploratory-unadjusted", "overall": {"count": 10, "wins": 3,
            "mean_probability": .35, "observed_win_rate": .3, "bias_predicted_minus_actual": .05,
            "bias_ci95": [-.1, .2], "bootstrap_samples_with_observations": 2000}, "odds_band_calibration": []}
    summary = {"bets": 10, "hits": 3, "stake": 1000, "payout": 700, "roi_percent": 70.,
        "race_coverage": 1., "max_drawdown_jpy": 400., "yearly": []}
    evaluation = {"prediction_precision": {}, "win_probability_calibration": {}, "separate_fixed_rule_roi": {}, "yearly_precision": {}}
    for period in review.PERIODS:
        evaluation["prediction_precision"][period] = {name: copy.deepcopy(metric) for name in methods}
        evaluation["win_probability_calibration"][period] = {name: {rule: copy.deepcopy(calibration)
            for rule in ("top1", "top1_ev105")} for name in methods}
        evaluation["separate_fixed_rule_roi"][period] = {"prediction_races": 10, "roi_races": 10,
            "unavailable_return_excluded_only_from_roi": 0, "policy_selection_performed": False,
            "historical_proxy_only": True, "summaries": {name: copy.deepcopy(summary) for name in policy_ids},
            "bootstrap": {"family_size": 9, "candidates": [{"policy_id": name,
                "roi_ci95": [30., 110.], "roi_ci_simultaneous": [15., 140.],
                "paired_profit_difference_ci_simultaneous": [-60., 60.], "bootstrap_samples_with_stake": 20000}
                for name in policy_ids]}}
    return {"evaluation": evaluation, "runtime": {}, "data_audit": {}}


def test_compact_report_keeps_seen_period_and_separate_roi_with_win_rmse_label():
    result = review.compact_results(fake_full_metrics())
    assert set(result["precision"]) == set(review.PERIODS)
    assert result["precision"]["SEEN_DIAGNOSTIC_2025_2026"]["rolling5_binary"]["rmse_win_indicator"] == .08 ** .5
    assert len(result["separate_roi"]["SEEN_DIAGNOSTIC_2025_2026"]["policies"]) == 9
    assert result["calibration"]["ALL_RETROSPECTIVE"]["expanding_binary"]["top1"]["overall"]["bias_ci95"] == [-.1, .2]
    policy = result["separate_roi"]["SEEN_DIAGNOSTIC_2025_2026"]["policies"][0]
    assert policy["simultaneous_interval_covers_100_percent"] is True
    assert policy["simultaneous_interval_strictly_above_100_percent"] is False
    assert policy["live_profit_edge_established"] is False


def test_compact_report_rejects_physically_mislabelled_probability_rmse():
    result = fake_full_metrics()
    result["evaluation"]["prediction_precision"][review.PERIODS[0]]["market_reference"]["physical_speed_rmse"] = .08 ** .5
    with pytest.raises(ValueError, match="probability-rmse-is-not-speed-rmse"):
        review.compact_results(result)


def test_compact_report_checks_fixed_stake_and_actual_report_payout_ratio():
    result = fake_full_metrics()
    result["evaluation"]["separate_fixed_rule_roi"][review.PERIODS[0]]["summaries"]["market_favorite"]["roi_percent"] = 100.
    with pytest.raises(ValueError, match="roi-payout-stake-invariant"):
        review.compact_results(result)


def test_compact_report_rejects_duplicate_nine_policy_ids():
    result = fake_full_metrics()
    records = result["evaluation"]["separate_fixed_rule_roi"][review.PERIODS[0]]["bootstrap"]["candidates"]
    records[1]["policy_id"] = "market_favorite"
    with pytest.raises(ValueError, match="exact-nine-policy-family-required"):
        review.compact_results(result)


def points_frame():
    import pandas as pd
    return pd.DataFrame({"race_id": ["a", "a", "a", "b", "b"],
        "race_date": ["20250101"] * 3 + ["20250102"] * 2, "horse_number": [1, 2, 3, 1, 2],
        "finish": [1, 2, 3, 2, 1], "probability": [.5, .3, .2, .3, .7],
        "odds": [2., 4., 5., 4., 2.]})


def test_independent_metrics_use_whole_race_brier_and_probability_rank():
    import math
    result = review.point_metrics(points_frame())
    assert result["race_count"] == 2 and result["runner_count"] == 5
    assert result["nll"] == pytest.approx((-math.log(.5) - math.log(.7)) / 2)
    assert result["brier"] == pytest.approx((.25 + .09 + .04 + .09 + .09) / 2)
    assert result["spearman"] == pytest.approx(1.)
    assert result["top1"] == 1.
    assert result["rmse_win_indicator"] ** 2 == pytest.approx(result["brier"] * 2 / 5)


def test_independent_selection_and_calibration_do_not_tune_thresholds():
    frame = points_frame()
    assert review.select_fixed(frame, "top1").horse_number.tolist() == [1, 2]
    chosen = review.select_fixed(frame, "top1_ev105")
    assert chosen.race_id.tolist() == ["b"]
    result = review.point_calibration(chosen)
    assert result["count"] == 1 and result["wins"] == 1
    assert result["bias_predicted_minus_actual"] == pytest.approx(-.3)
    assert review.point_calibration(chosen.iloc[0:0])["mean_probability"] is None


def test_independent_settlement_is_from_payout_mapping_not_odds_times_stake():
    frame = points_frame()
    result = review.point_roi(frame, review.select_fixed(frame, "top1"), {"a": 220., "b": 150.})
    assert result["bets"] == 2 and result["hits"] == 2
    assert result["stake"] == 200 and result["payout"] == 370
    assert result["roi_percent"] == 185.
    with pytest.raises(ValueError, match="winning-payout-unavailable"):
        review.point_roi(frame, review.select_fixed(frame, "top1"), {"a": 220.})


def test_independent_payout_read_only_key_validation(tmp_path):
    import sqlite3
    snapshot = tmp_path / "snapshot.db"
    with sqlite3.connect(snapshot) as connection:
        connection.execute("CREATE TABLE return_tables_ultimate (race_id TEXT, combinations TEXT, payout INTEGER, bet_type TEXT)")
        connection.executemany("INSERT INTO return_tables_ultimate VALUES(?,?,?,?)", [
            ("a", "1", 220, "単勝"), ("b", "1", 300, "単勝")])
    before = review.digest(snapshot)
    assert review.read_return_mapping(points_frame(), snapshot) == {"a": 220.}
    assert review.digest(snapshot) == before


def test_evaluation_only_saved44_runtime_is_affirmative(tmp_path):
    saved = (tmp_path / "saved-v2").resolve()
    aggregate = {"worker_study_path": str(saved), "runtime": {
        "mode": "saved44-evaluation-only", "new_fits_started": 0,
        "fit_wall_seconds_this_run": 0, "saved_v2_artifacts_reused": True,
        "validated_saved_worker_count": 44}}
    receipt = review.evaluation_only_receipt(aggregate, saved)
    assert receipt["new_fits_started"] == 0
    assert receipt["saved_v2_artifacts_reused"] is True
    assert receipt["validated_saved_worker_count"] == 44


def test_evaluation_only_cannot_assume_missing_fit_evidence(tmp_path):
    with pytest.raises(ValueError, match="saved44-no-fit-receipt"):
        review.evaluation_only_receipt({"worker_study_path": str(tmp_path)}, tmp_path.resolve())


def test_evaluation_only_cannot_rebind_other_workers(tmp_path):
    aggregate = {"worker_study_path": str(tmp_path / "other"), "runtime": {
        "mode": "saved44-evaluation-only", "new_fits_started": 0,
        "fit_wall_seconds_this_run": 0, "saved_v2_artifacts_reused": True,
        "validated_saved_worker_count": 44}}
    with pytest.raises(ValueError, match="saved44-no-fit-receipt"):
        review.evaluation_only_receipt(aggregate, tmp_path.resolve())


def uncertainty_fixture():
    full = fake_full_metrics()
    protocol = {"probability_bootstrap_repetitions": 2000, "roi_bootstrap_repetitions": 20000,
        "bootstrap_seed": 20261012, "odds_bands": [1, 3, 5, 10, 20, 30, None]}
    labels = ["1-<3", "3-<5", "5-<10", "10-<20", "20-<30", "30+"]
    scope = {"method": "whole-date-cluster-year-stratified-bootstrap", "confidence": .95,
        "repetitions": 2000, "seed": 20261012, "eligible_no_bet_dates_included": True,
        "interval_scope": "secondary-exploratory-unadjusted", "fits_calibrator": False,
        "changes_policies": False, "eligible_for_ev": False,
        "odds_band_boundaries": protocol["odds_bands"], "odds_bands": labels}
    def record():
        return {"count": 1, "mean_probability_ci95": [.2, .4], "observed_win_rate_ci95": [0., 1.],
            "bias_ci95": [-.8, .4], "bootstrap_samples_with_observations": 2000}
    for period in review.PERIODS:
        for method in ("market_reference", *review.CANDIDATES):
            metric = full["evaluation"]["prediction_precision"][period][method]
            selected = full["evaluation"]["win_probability_calibration"][period][method]
            for item in (metric, *selected.values()):
                item.update(scope)
                item["odds_band_calibration"] = [{**record(), "odds_band": label} for label in labels]
                if "overall" in item:
                    item["overall"].update(record())
            for name in ("nll", "brier", "spearman", "top1"):
                metric[name + "_ci95"] = [0., 2.]
            metric["comparison_vs_reference"] = {"delta_nll_ci95": [-.1, .1], "delta_brier_ci95": [-.1, .1]}
        boot = full["evaluation"]["separate_fixed_rule_roi"][period]["bootstrap"]
        boot.update(method="paired-whole-date-cluster-year-stratified-bootstrap", repetitions=20000,
            seed=20261012, reference_id="market_favorite", all_cohort_dates_included=True,
            common_candidate_weights=True, simultaneous_confidence=1. - .05 / 9)
        for item in boot["candidates"]:
            item["paired_profit_difference_ci95"] = [-20., 20.]
    return full, protocol


def test_reported_uncertainty_scope_is_not_recomputed_or_globalized():
    full, protocol = uncertainty_fixture()
    audit = review.uncertainty_scope_audit(full, protocol)
    assert audit["calibration_records_checked"] == 300
    assert audit["reported_intervals_recomputed"] is False
    assert "within each period only" in audit["roi_coverage_guarantee_scope"]


def test_uncertainty_audit_rejects_narrower_simultaneous_interval():
    full, protocol = uncertainty_fixture()
    full["evaluation"]["separate_fixed_rule_roi"][review.PERIODS[0]]["bootstrap"]["candidates"][0]["roi_ci_simultaneous"] = [50., 90.]
    with pytest.raises(ValueError, match="must-contain-ordinary"):
        review.uncertainty_scope_audit(full, protocol)
