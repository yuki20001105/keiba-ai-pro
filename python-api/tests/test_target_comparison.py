import math
import time

import numpy as np
import pandas as pd
import pytest


def _outcomes():
    return pd.DataFrame({
        "race_id": ["a"] * 3 + ["b"] * 3,
        "race_date": ["20230101"] * 3 + ["20230102"] * 3,
        "horse_number": [1, 2, 3] * 2, "finish": [1, 2, 3] * 2,
    })


def test_common_race_metrics_have_hand_checked_nll_brier_rank_and_top1():
    from training.target_comparison import race_metrics

    probabilities = np.array([.7, .2, .1, .2, .6, .2])
    summary, races = race_metrics(_outcomes(), np.log(probabilities), probabilities)
    assert summary["nll"] == pytest.approx(-math.log(.14) / 2)
    assert summary["brier"] == pytest.approx(.59)
    assert summary["top1"] == .5
    assert summary["spearman"] == pytest.approx(.5)
    assert summary["top3_recall"] == 1
    assert len(races) == 2


def test_probabilities_are_normalized_within_race_and_stable_to_offsets():
    from training.target_comparison import race_probabilities

    scores = np.log([.7, .2, .1, .2, .6, .2])
    ids = _outcomes().race_id
    probabilities = race_probabilities(scores, ids, 1)
    np.testing.assert_allclose(probabilities, [.7, .2, .1, .2, .6, .2])
    np.testing.assert_allclose(
        race_probabilities(scores + np.array([1000] * 3 + [-1000] * 3), ids, 1), probabilities,
    )
    with pytest.raises(ValueError):
        race_probabilities(np.array([1.]), np.array(["a"]), 1)


def test_temperature_fitting_accepts_cal_only_and_improves_overconfident_scores():
    from training.target_comparison import calibrate, race_metrics, race_probabilities

    frame = pd.concat([_outcomes().assign(race_id=lambda f: f.race_id + str(i))
                       for i in range(10)], ignore_index=True)
    probabilities = np.tile([.7, .2, .1, .2, .6, .2], 10)
    overconfident = 4 * np.log(probabilities)
    audit = calibrate(frame, overconfident)
    calibrated, _ = race_metrics(frame, overconfident,
                                race_probabilities(overconfident, frame.race_id, audit["temperature"]))
    before, _ = race_metrics(frame, overconfident, race_probabilities(overconfident, frame.race_id, 1))
    assert calibrated["nll"] < before["nll"]
    assert audit["fit_uses_test"] is False
    assert audit["calibration_races"] == 20


def test_year_splits_exclude_calibration_validation_and_test_from_training():
    from training.target_comparison import partition_masks

    frame = pd.DataFrame({"race_date": ["20160101", "20170101", "20210101",
                                      "20220101", "20230101", "20260101"]})
    train, calibration, evaluation = partition_masks(frame, 2023)
    assert np.flatnonzero(train).tolist() == [1, 2]
    assert np.flatnonzero(calibration).tolist() == [3]
    assert np.flatnonzero(evaluation).tolist() == [4]


def test_final_calibration_half_year_is_never_used_for_development_selection():
    from training.target_comparison import partition_masks

    frame = pd.DataFrame({"race_date": ["20231231", "20241231", "20250630",
                                      "20250701", "20251231", "20260101"]})
    _, _, development = partition_masks(frame, 2025)
    final_train, final_cal, final_test = partition_masks(frame, 2026)
    assert np.flatnonzero(development).tolist() == [2]
    assert np.flatnonzero(final_train).tolist() == [0, 1, 2]
    assert np.flatnonzero(final_cal).tolist() == [3, 4]
    assert np.flatnonzero(final_test).tolist() == [5]


def test_race_balanced_loss_preserves_total_scale_for_fixed_l2_regularization():
    from training.target_comparison import training_weights

    frame = pd.DataFrame({"race_id": ["a"] * 2 + ["b"] * 4})
    weights = training_weights(frame, "condition_speed")
    np.testing.assert_allclose(weights, [1.5, 1.5, .75, .75, .75, .75])
    assert weights.sum() == 6
    assert weights[:2].sum() == weights[2:].sum()
    assert training_weights(frame, "current_speed") is None


def test_unavailable_label_removes_entire_race_from_every_candidate():
    from training.target_comparison import common_cohort_mask

    frame = _outcomes()
    condition = pd.Series([0., 1., np.nan, 0., 1., -1.])
    assert common_cohort_mask(frame, condition).tolist() == [False] * 3 + [True] * 3
    frame.loc[5, "finish"] = 1
    assert not common_cohort_mask(frame, condition).any()


def test_approval_is_bound_to_exact_task_not_actor_or_different_goal():
    from experiments.common import json_sha256
    from training.target_comparison import check_approval

    task = {"goal": "a", "budget": 40}
    check_approval(task, {"actor": "copilot-delegated", "task_sha256": json_sha256(task)})
    with pytest.raises(ValueError):
        check_approval(task, {"actor": "copilot-delegated",
                              "task_sha256": json_sha256({"goal": "a", "budget": 41})})


def test_paired_comparison_rejects_changed_cohorts():
    from training.target_comparison import paired_comparison

    _, base = __import__("training.target_comparison", fromlist=["race_metrics"]).race_metrics(
        _outcomes(), np.log([.7, .2, .1, .2, .6, .2]), np.array([.7, .2, .1, .2, .6, .2]),
    )
    with pytest.raises(ValueError):
        paired_comparison(base, base.iloc[:1])


def test_repeated_races_on_one_day_do_not_fake_twenty_independent_days():
    from training.target_comparison import paired_comparison

    base = pd.DataFrame({"race_id": [str(i) for i in range(100)],
                         "race_date": "20230101", "nll": 2., "brier": .8,
                         "top1": 0., "spearman": .4, "top3_recall": .4})
    better = base.assign(nll=1., top1=1.)
    result = paired_comparison(base, better)
    assert result["paired_days"] == 1
    assert result["passes"] is False


def test_decision_requires_improvement_not_just_a_finite_trained_model():
    from training.target_comparison import paired_comparison

    base = pd.DataFrame({"race_id": [str(i) for i in range(30)],
                         "race_date": pd.date_range("20230101", periods=30).strftime("%Y%m%d"),
                         "nll": 2., "brier": .8, "top1": .4,
                         "spearman": .4, "top3_recall": .4})
    assert paired_comparison(base, base.assign(nll=1.9))["passes"] is True
    assert paired_comparison(base, base.assign(nll=2.1))["passes"] is False
    assert paired_comparison(base, base.assign(nll=1.9, top1=.39))["passes"] is False


def test_prediction_verification_rejects_wrong_horse_identity_despite_correct_scores():
    from training.target_comparison import validate_prediction_table

    target = _outcomes()
    scores = np.log([.7, .2, .1, .2, .6, .2])
    probabilities = np.exp(scores)
    saved = target.assign(score=scores, probability=probabilities)
    validate_prediction_table(target, saved, scores, probabilities)
    saved.loc[0, "horse_number"] = 99
    with pytest.raises((ValueError, AssertionError)):
        validate_prediction_table(target, saved, scores, probabilities)
    saved = target.assign(score=scores, probability=probabilities)
    saved.loc[0, "finish"] = 2
    with pytest.raises((ValueError, AssertionError)):
        validate_prediction_table(target, saved, scores, probabilities)


def test_changed_disk_formula_cannot_be_attested_as_the_old_loaded_formula(tmp_path, monkeypatch):
    from training import target_comparison as comparison

    copied = tmp_path / "target_comparison.py"
    copied.write_bytes(__import__("pathlib").Path(comparison.__file__).read_bytes())
    formulas = tmp_path / "performance_targets.py"
    formulas.write_bytes(__import__("pathlib").Path(comparison.__file__).with_name("performance_targets.py").read_bytes())
    monkeypatch.setattr(comparison, "__file__", str(copied))
    comparison._sources()
    formulas.write_bytes(formulas.read_bytes() + b"\n# formula changed after import\n")
    with pytest.raises(ValueError):
        comparison._sources()


def test_runtime_supervision_rejects_expired_deadline_and_ram_pressure(monkeypatch):
    from training.target_comparison import check_run_limits
    import psutil

    with pytest.raises(TimeoutError):
        check_run_limits(time.time() - 1)
    monkeypatch.setattr(psutil, "virtual_memory", lambda: type("Memory", (), {"available": 3 * 1024**3})())
    with pytest.raises(RuntimeError):
        check_run_limits(time.time() + 60)


def test_failure_cleanup_stops_only_explicitly_owned_workers():
    import multiprocessing
    from training.target_comparison import terminate_owned_workers

    context = multiprocessing.get_context("spawn")
    owned = context.Process(target=time.sleep, args=(60,))
    unrelated = context.Process(target=time.sleep, args=(60,))
    owned.start()
    unrelated.start()
    try:
        terminate_owned_workers([owned])
        assert not owned.is_alive()
        assert unrelated.is_alive()
    finally:
        for process in [owned, unrelated]:
            if process.is_alive():
                process.terminate()
            process.join(timeout=10)


def test_teacher_verification_rejects_hash_bound_non_sqlite_snapshot(tmp_path):
    from training.target_comparison import rebuild_inputs

    snapshot = tmp_path / "snapshot.db"
    snapshot.write_bytes(b"hash-bound but not a SQLite database")
    with pytest.raises(Exception):
        rebuild_inputs(snapshot)


def _selection_cases(root):
    from training.target_comparison import ARMS, PRESETS

    rows = []
    for arm in ARMS:
        for preset in PRESETS:
            for year in [2023, 2024, 2025]:
                loss = 2.2
                if arm == "current_speed" and preset["id"] == "standard":
                    loss = 2.
                if arm == "win_binary" and preset["id"] == "compact":
                    loss = 1.8
                name = f"{arm}-{preset['id']}-{year}"
                directory = root / "cases" / name
                directory.mkdir(parents=True)
                frame = pd.DataFrame({"race_id": [f"{year}a", f"{year}b"],
                                      "race_date": [f"{year}0101", f"{year}0102"],
                                      "nll": loss, "brier": .8, "top1": .4,
                                      "spearman": .4, "top3_recall": .4})
                frame.to_parquet(directory / "race-metrics.parquet", index=False)
                rows.append({"name": name, "arm": arm, "preset": preset, "evaluation_year": year,
                             "metrics": {"nll": loss}})
    return rows


def test_independent_selection_chooses_development_winner_and_rejects_forged_decision(tmp_path):
    from training.target_comparison import select_development, validate_selection_and_decision

    rows = _selection_cases(tmp_path)
    summaries, selection = select_development(tmp_path, rows)
    assert selection["baseline"]["preset"]["id"] == "standard"
    assert selection["challenger"]["arm"] == "win_binary"
    assert selection["challenger"]["preset"]["id"] == "compact"
    result = {"development_summary": summaries,
              "development_comparison": selection["development_comparison"],
              "final_comparison": {"passes": False}, "decision": "research_candidate_supported"}
    with pytest.raises(ValueError):
        validate_selection_and_decision(tmp_path, rows, selection, result)
    result["decision"] = "retain_current_target"
    validate_selection_and_decision(tmp_path, rows, selection, result)
    selection["challenger"] = selection["baseline"]
    with pytest.raises(ValueError):
        validate_selection_and_decision(tmp_path, rows, selection, result)


def _training_database(path):
    from experiments.dataset import SOURCE_COLUMNS
    from test_db_shared_features import write_db

    rows = []
    for year in range(2016, 2027):
        for index, day in enumerate(pd.date_range(f"{year}0105", f"{year}1220", periods=84)):
            surface = "turf" if index % 2 == 0 else "dirt"
            distance = 1600 if surface == "turf" else 1800
            for number in range(1, 5):
                finish = (number + index) % 4 + 1
                rows.append({
                    "race_id": f"{year}0501{index // 12 + 1:02d}{index % 12 + 1:02d}",
                    "race_date": day.strftime("%Y%m%d"), "horse_id": f"horse-{number}",
                    "horse_number": number, "distance": distance, "surface": surface,
                    "venue": "東京", "num_horses": 4, "finish": finish,
                    "time_seconds": distance / (16.2 - finish * .15), "last_3f_time": 34. + number,
                    "bracket_number": number, "age": 4, "sex": "牡", "burden_weight": 57.,
                    "horse_weight": 470. + number, "horse_weight_change": float(number - 2),
                    "field_condition": "良", "weather": "晴", "race_class": "未勝利",
                    "odds": 2. + number, "popularity": number, "jockey_id": "j1",
                    "trainer_id": "t1", "sire": "父", "damsire": "母父",
                })
    return write_db(path, pd.DataFrame(rows, columns=SOURCE_COLUMNS))


def test_teacher_reconstruction_rejects_wrong_labels_even_when_their_hash_is_bound(tmp_path):
    from experiments.common import file_sha256
    from training.target_comparison import prepare, validate_prepared_inputs

    source = _training_database(tmp_path / "source.db")
    root = tmp_path / "target-teacher-test"
    task = prepare(root, source)
    labels = pd.read_parquet(root / "labels.parquet")
    labels["condition_speed"] = 999.
    labels.to_parquet(root / "labels.parquet", index=False)
    task["files"]["labels.parquet"] = file_sha256(root / "labels.parquet")
    with pytest.raises(AssertionError):
        validate_prepared_inputs(root, task)


def test_real_spawned_models_and_full_independent_verification(tmp_path):
    from experiments.common import json_sha256
    from experiments.search import freeze_json
    from training.target_comparison import prepare, run, verify

    source = _training_database(tmp_path / "source.db")
    root = tmp_path / "target-spawn-test"
    task = prepare(root, source)
    freeze_json(root / "approval.json", {"actor": "isolated-pytest-fixture",
                                       "task_sha256": json_sha256(task)})
    result = run(root)
    verification = verify(root)
    assert result["fit_count"] == 38
    assert verification["verified"] is True
    assert verification["models_reloaded"] == 38
    assert verification["promotion_eligible"] is False
