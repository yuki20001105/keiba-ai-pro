"""Synthetic data only: these tests never train on production/research outcomes."""
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from market_edge.modeling import fit_model as old_fit, predict_model as old_predict
from market_segments.modeling import (FEATURES, MARKET_FEATURES, STABLE_FEATURES,
                                     predict_model, sha256, train_predict)
from market_segments import modeling


def write(path, data):
    path.write_text(json.dumps(data, allow_nan=False), encoding="utf-8")


def frame_fixture(seed=True):
    rows = []
    periods = [("20210301", "calibration_seed"), ("20210701", "meta_fit"),
               ("20211001", "development"), ("20220101", "validation")]
    for period, phase in periods if seed else periods[1:]:
        for r in range(8):
            for h, p in enumerate([.6, .3, .1], 1):
                row = dict(race_id=f"{period}{r:04d}", race_date=period, horse_number=h,
                           phase=phase, expected_runner_count=3, finish=h, fold_year=int(period[:4]),
                           fold_train_date_to=f"{int(period[:4]) - 1}1231",
                           normalized_market_probability=p, log_odds=np.log(1 / p), market_rank=h,
                           h04_score=p, h04_rank=h, h04_probability=p, rank_difference=0.,
                           probability_difference=0., field_size=3)
                for i, column in enumerate(STABLE_FEATURES):
                    row[column] = np.nan if h == 3 else float(i + h + r % 2)
                if phase == "calibration_seed":
                    row["h04_probability"], row["probability_difference"] = np.nan, np.nan
                rows.append(row)
    return pd.DataFrame(rows)


def fixture_contract(tmp_path, frame=None, spec_update=None, ready=True, released=True):
    tmp_path.mkdir(parents=True, exist_ok=True)
    data = tmp_path / "data.parquet"
    (frame if frame is not None else frame_fixture()).to_parquet(data, index=False)
    quality = tmp_path / "quality"
    quality.mkdir()
    summary = quality / "quality_summary.json"
    write(summary, {"MODEL_RESEARCH_READY": ready})
    witness = quality / "clean-proof.json"
    write(witness, {"verified": True})
    manifest = quality / "artifact_manifest.json"
    write(manifest, {"files": {"quality_summary.json": sha256(summary), "clean-proof.json": sha256(witness)}, "code": {}})
    release = tmp_path / "quality-release.json"
    write(release, {"schema": "market-h04-quality-release-v1", "released": released,
                    "quality_root": str(quality.resolve()), "quality_summary_sha256": sha256(summary),
                    "quality_manifest_sha256": sha256(manifest)})
    spec = {"dataset_sha256": sha256(data), "groups": {
        "A": {"status": "available", "columns": []},
        "B": {"status": "available", "columns": FEATURES},
        "C": {"status": "unavailable", "reason": "Historical training availability not verified"},
        "D": {"status": "unavailable", "reason": "Historical raw speed availability not verified"},
        "E": {"status": "unavailable", "reason": "Both additional source groups unavailable"},
        "F": {"status": "available", "columns": MARKET_FEATURES + STABLE_FEATURES, "asof_verified": True}},
        "feature_evidence": {c: {"asof_verified": True, "source_policy": "strict source event date before target"}
                             for c in STABLE_FEATURES}}
    if spec_update:
        spec_update(spec)
    spec_path = tmp_path / "feature_spec.json"
    write(spec_path, spec)
    plan = tmp_path / "experiment_plan.json"
    write(plan, {"feature_spec_sha256": sha256(spec_path), "data": {"dataset_sha256": sha256(data)},
                 "phase1_gate": {"quality_summary_path": str(summary), "quality_summary_sha256": sha256(summary),
                                 "quality_manifest_path": str(manifest), "quality_manifest_sha256": sha256(manifest),
                                 "quality_release_path": str(release), "quality_release_sha256": sha256(release)}})
    return dict(dataset_path=data, feature_spec_path=spec_path, plan_path=plan,
                expected_plan_sha256=sha256(plan), expected_feature_spec_sha256=sha256(spec_path))


def run(contract, output, group="B", model="logistic", fold="development"):
    return train_predict(**contract, output=output, group=group, model=model, fold=fold)


def test_raw_market_baseline_no_fit_and_same_cohort(tmp_path):
    frame = frame_fixture()
    contract = fixture_contract(tmp_path, frame)
    result = run(contract, tmp_path / "A", "A", "market")
    prediction = pd.read_parquet(tmp_path / "A/predictions.parquet")
    assert result["train_seconds"] == 0 and not (tmp_path / "A/artifacts").exists()
    assert len(prediction) == 24 and result["calibration_seed_rows_excluded_from_model_fit_and_evaluation"] == 24
    assert np.array_equal(prediction.probability, frame.loc[frame.phase.eq("development"), "normalized_market_probability"])


def test_B_exact_old_feature_and_prediction_parity(tmp_path):
    frame = frame_fixture()
    contract = fixture_contract(tmp_path, frame)
    result = run(contract, tmp_path / "B")
    old_fit(frame.loc[frame.phase.eq("meta_fit")].reset_index(drop=True), "logistic", "cpu", tmp_path / "old")
    expected = old_predict(frame.loc[frame.phase.eq("development")].reset_index(drop=True), tmp_path / "old")
    actual = pd.read_parquet(tmp_path / "B/predictions.parquet").probability
    assert np.array_equal(expected, actual)
    assert result["features"] == FEATURES
    assert result["train_date_to"] == "20210701"


@pytest.mark.parametrize("family", ["logistic", "lightgbm"])
def test_F_missing_rows_kept_medians_train_only_and_cpu_fixed(tmp_path, family):
    frame = frame_fixture()
    contract = fixture_contract(tmp_path, frame)
    result = run(contract, tmp_path / family, "F", family)
    artifact = json.loads((tmp_path / family / "artifacts/model.json").read_text())
    assert result["train_rows"] == result["evaluation_rows"] == 24
    assert artifact["preprocessing"]["indicator_columns"] == STABLE_FEATURES
    assert result["input_feature_count"] == 12 and result["feature_count"] == 20
    train = frame.loc[frame.phase.eq("meta_fit")]
    assert artifact["preprocessing"]["medians"][4:] == train[STABLE_FEATURES].median().tolist()
    assert not set(FEATURES[3:8]) & set(artifact["features"])
    inference = frame.loc[frame.phase.eq("development")].reset_index(drop=True).drop(columns="finish")
    p = predict_model(inference, tmp_path / family / "artifacts")
    assert np.allclose(pd.Series(p).groupby(inference.race_id).sum(), 1)
    if family == "lightgbm":
        assert artifact["rounds"] == 150
        assert artifact["params"]["num_threads"] == 2
        assert artifact["params"]["device_type"] == "cpu"
        assert artifact["params"]["num_leaves"] == 7
        assert artifact["params"]["lambda_l2"] == 10


def test_validation_values_labels_do_not_change_fit(tmp_path):
    original = frame_fixture()
    changed = original.copy()
    changed.loc[changed.phase.eq("development"), STABLE_FEATURES] = 987654.
    changed.loc[changed.phase.eq("development"), "finish"] = 4 - changed.loc[changed.phase.eq("development"), "finish"]
    left = fixture_contract(tmp_path / "left", original)
    right = fixture_contract(tmp_path / "right", changed)
    run(left, tmp_path / "left/model", "F")
    run(right, tmp_path / "right/model", "F")
    a = json.loads((tmp_path / "left/model/artifacts/model.json").read_text())
    b = json.loads((tmp_path / "right/model/artifacts/model.json").read_text())
    a.pop("train_seconds")
    b.pop("train_seconds")
    assert a == b


@pytest.mark.parametrize("group", ["C", "D", "E"])
def test_unavailable_groups_explicit_skip_no_baseline_copy(tmp_path, group):
    contract = fixture_contract(tmp_path)
    result = run(contract, tmp_path / "skip", group)
    assert result["status"] == "skipped" and result["skip_reason"]
    assert [p.name for p in (tmp_path / "skip").iterdir()] == ["result.json"]


def test_always_missing_fit_feature_skips_even_if_validation_available(tmp_path):
    frame = frame_fixture()
    frame.loc[frame.phase.eq("meta_fit"), STABLE_FEATURES[0]] = np.nan
    contract = fixture_contract(tmp_path, frame)
    result = run(contract, tmp_path / "F", "F")
    assert result["status"] == "skipped" and "Always missing" in result["skip_reason"]
    assert not (tmp_path / "F/predictions.parquet").exists()


def test_missing_asof_evidence_skips(tmp_path):
    contract = fixture_contract(tmp_path, spec_update=lambda spec: spec["feature_evidence"].pop(STABLE_FEATURES[0]))
    assert run(contract, tmp_path / "F", "F")["status"] == "skipped"


@pytest.mark.parametrize("ready,released", [(False, True), (True, False)])
def test_failed_phase1_blocks_before_any_data_load(tmp_path, monkeypatch, ready, released):
    contract = fixture_contract(tmp_path, ready=ready, released=released)
    monkeypatch.setattr(pd, "read_parquet", lambda *a, **k: pytest.fail("Read modeling data before quality gate"))
    with pytest.raises(ValueError, match="Phase 1"):
        run(contract, tmp_path / "blocked")


def test_modified_quality_artifact_blocks_before_fit(tmp_path):
    contract = fixture_contract(tmp_path)
    write(tmp_path / "quality/clean-proof.json", {"verified": False})
    with pytest.raises(ValueError, match="manifest artifact/source hash"):
        run(contract, tmp_path / "blocked")


def test_missing_final_manifest_never_accepts_loose_pass_summary(tmp_path):
    contract = fixture_contract(tmp_path)
    (tmp_path / "quality/artifact_manifest.json").unlink()
    with pytest.raises(FileNotFoundError):
        run(contract, tmp_path / "blocked")
    assert not (tmp_path / "blocked").exists()


def test_release_bound_to_a_different_quality_root_is_rejected(tmp_path):
    contract = fixture_contract(tmp_path)
    release_path = tmp_path / "quality-release.json"
    release = json.loads(release_path.read_text())
    release["quality_root"] = str(tmp_path / "other-root")
    write(release_path, release)
    plan = json.loads(contract["plan_path"].read_text())
    plan["phase1_gate"]["quality_release_sha256"] = sha256(release_path)
    write(contract["plan_path"], plan)
    contract["expected_plan_sha256"] = sha256(contract["plan_path"])
    with pytest.raises(ValueError, match="release is absent or inconsistent"):
        run(contract, tmp_path / "blocked")


def test_changed_quality_contract_during_fit_prevents_result_publication(tmp_path, monkeypatch):
    contract = fixture_contract(tmp_path)
    original_fit = modeling._fit_model

    def changed_after_fit(*args):
        result = original_fit(*args)
        write(tmp_path / "quality/clean-proof.json", {"verified": False})
        return result

    monkeypatch.setattr(modeling, "_fit_model", changed_after_fit)
    with pytest.raises(ValueError, match="manifest artifact/source hash"):
        run(contract, tmp_path / "unfinished")
    assert not (tmp_path / "unfinished/result.json").exists()
    assert not (tmp_path / "unfinished/predictions.parquet").exists()


@pytest.mark.parametrize("name", ["plan_path", "feature_spec_path", "dataset_path"])
def test_hash_mismatch_before_data_load(tmp_path, monkeypatch, name):
    contract = fixture_contract(tmp_path)
    with contract[name].open("ab") as stream:
        stream.write(b" ")
    monkeypatch.setattr(pd, "read_parquet", lambda *a, **k: pytest.fail("Read unverified modeling data"))
    with pytest.raises(ValueError, match="hash mismatch"):
        run(contract, tmp_path / "blocked")


@pytest.mark.parametrize("field,value", [("phase", "meta_fit"), ("race_date", "20230101"),
                                        ("horse_number", 19), ("finish", 4),
                                        ("expected_runner_count", 4), ("fold_train_date_to", "20211231")])
def test_bad_cohort_and_chronology_fail_closed(tmp_path, field, value):
    frame = frame_fixture()
    frame.loc[frame.phase.eq("development"), field] = value
    contract = fixture_contract(tmp_path, frame)
    with pytest.raises(ValueError):
        run(contract, tmp_path / "blocked")


def test_F_definition_cannot_change_after_predeclaration(tmp_path):
    contract = fixture_contract(tmp_path, spec_update=lambda spec: spec["groups"]["F"]["columns"].append("safe_age"))
    with pytest.raises(ValueError, match="eight predeclared"):
        run(contract, tmp_path / "blocked", "F")


def test_output_immutable_and_validation_refit_H2_only(tmp_path):
    contract = fixture_contract(tmp_path, frame_fixture(seed=False))
    result = run(contract, tmp_path / "model", fold="validation")
    assert result["train_rows"] == 48 and result["evaluation_rows"] == 24
    assert result["train_date_to"] == "20211001"
    assert result["period_label"] == "research-known validation"
    with pytest.raises(FileExistsError):
        run(contract, tmp_path / "model", fold="validation")


def test_worker_rejects_gpu_without_running_job():
    script = Path(__file__).resolve().parents[2] / "scripts/run_market_edge_segments_worker.py"
    completed = subprocess.run([sys.executable, str(script), "--device", "gpu"], capture_output=True, text=True)
    assert completed.returncode == 2
    assert "invalid choice" in completed.stderr
