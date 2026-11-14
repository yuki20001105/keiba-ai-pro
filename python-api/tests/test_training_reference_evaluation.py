import hashlib
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import joblib
import numpy as np
import pandas as pd
import pytest

from test_paper_simulation import model, official


class IdentityEncoder:
    def transform(self, frame):
        return frame


def inputs():
    rows = []
    for race_id, day in (("train", "20220604"), ("cal", "20240810"), ("test", "20240811")):
        rows.extend({"race_id": race_id, "race_date": day, "horse_number": i, "horse_id": str(i),
                     "num_horses": 5, "odds": odds, "finish": i}
                    for i, odds in enumerate([2, 5, 8, 12, 20], 1))
    frame = pd.DataFrame(rows)
    return frame, pd.DataFrame(1.0, index=frame.index, columns=["a", "b", "c"])


def build(monkeypatch):
    from training import db_normalized_registry
    from training.reference_evaluation import build_training_reference
    def predict(bundle, features, race_ids, **kwargs):
        assert list(features.columns) == ["a", "b", "c"]
        assert set(race_ids) == {"test"}
        return np.zeros(5), np.array([.4, .25, .15, .12, .08])
    monkeypatch.setattr(db_normalized_registry, "prediction_module",
                        lambda bundle: SimpleNamespace(predict_encoded_shared_scores=predict))
    frame, features = inputs()
    receipt = {"date_from": "20240811", "date_to": "20240811",
               "race_count": 1, "runner_count": 5, "date_count": 1}
    bundle = model()
    bundle["encoder"] = IdentityEncoder()
    report = build_training_reference(bundle, frame, features, receipt, "model-test")
    return bundle, report


def test_training_reference_uses_saved_schema_and_only_untouched_test(monkeypatch):
    bundle, report = build(monkeypatch)
    assert set(report["records"]) == {"test"}
    value = report["records"]["test"]
    assert value["race_info"]["date"] == "20240811"
    assert value["input_diagnostics"]["current_outcomes_consumed"] is False
    assert value["predictions"][0]["p_norm"] == .4
    assert "finish" not in value["predictions"][0]
    assert "training_reference_evaluation" not in bundle
    from training.reference_evaluation import build_training_reference
    frame, features = inputs()
    with pytest.raises(ValueError, match="test-cohort"):
        build_training_reference(bundle, frame, features,
            {"date_from": "20240810", "date_to": "20240811", "race_count": 1,
             "runner_count": 5, "date_count": 1}, "model-test")


@pytest.mark.parametrize("same_day", [False, True])
@pytest.mark.parametrize("retry", [False, True])
def test_completed_owner_training_import_is_idempotent_and_settles_without_prediction(tmp_path, monkeypatch, same_day, retry):
    from training.reference_evaluation import sync_training_evaluations
    from betting.paper_simulation import list_paper_performance
    import app_config
    if same_day:
        import betting.paper_simulation as simulation
        monkeypatch.setattr(simulation, "_now", lambda value: datetime(2024, 8, 11, 12, tzinfo=timezone.utc))
    models = tmp_path / "models"
    models.mkdir()
    monkeypatch.setattr(app_config, "MODELS_DIR", models)
    monkeypatch.setattr(app_config, "APP_ENV", "test")
    jobs = tmp_path / "jobs.db"
    ledger, results = tmp_path / "performance.db", tmp_path / "results.db"
    bundle, report = build(monkeypatch)
    path = models / "model_speed_deviation_lightgbm_dbshared_example.joblib"
    report["records"]["test"]["model_id"] = path.stem
    bundle["training_reference_evaluation"] = report
    joblib.dump(bundle, path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    summary = json.dumps({"evaluation": {"reference_accounting": {"schema": report["schema"]}}})
    with sqlite3.connect(jobs) as db:
        db.executescript(
            "CREATE TABLE local_retrain_jobs(job_id TEXT,owner_id TEXT,state TEXT);"
            "CREATE TABLE local_retrain_publications(job_id TEXT,artifact_sha256 TEXT,"
            "published_path TEXT,state TEXT,result_json TEXT);"
        )
        db.execute("INSERT INTO local_retrain_jobs VALUES ('job','alice','completed')")
        db.execute("INSERT INTO local_retrain_publications VALUES (?,?,?,'published',?)",
                   ("job", sha, str(path), summary))
    official(results, "test", "20240811")
    with sqlite3.connect(results) as db:
        db.execute("UPDATE race_results_ultimate SET data=json_set(data,'$.horse_id',"
                   "CAST(json_extract(data,'$.horse_number') AS TEXT))")
    sync_training_evaluations(ledger, "bob", jobs_path=jobs)
    assert not ledger.exists()
    if retry:
        import betting.paper_simulation as simulation
        original = simulation.record_paper_prediction
        def interrupted(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("interrupted import")
        monkeypatch.setattr(simulation, "record_paper_prediction", interrupted)
        with pytest.raises(RuntimeError, match="interrupted import"):
            sync_training_evaluations(ledger, "alice", jobs_path=jobs)
        with sqlite3.connect(ledger) as db:
            assert db.execute("SELECT COUNT(*) FROM paper_simulation_records").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM paper_simulation_imports").fetchone()[0] == 0
        monkeypatch.setattr(simulation, "record_paper_prediction", original)
    sync_training_evaluations(ledger, "alice", jobs_path=jobs)
    data = list_paper_performance(ledger, results, "alice", origin="combined")
    assert data["total_count"] == 1
    assert data["model_performance"]["models"][0]["cohorts"]["evaluation"]["total_return"] == 29000
    assert data["model_performance"]["origin_counts"] == {"historical": 1}
    monkeypatch.setattr(joblib, "load", lambda *args: pytest.fail("completed import loaded a model again"))
    sync_training_evaluations(ledger, "alice", jobs_path=jobs)
    assert list_paper_performance(ledger, results, "alice", origin="combined")["total_count"] == 1


@pytest.mark.parametrize("name", ["db_shared_fit", "db_shared_optimized", "db_shared_grouped", "db_context_fit"])
def test_training_reference_accepts_actual_saved_prediction_schemas(monkeypatch, name):
    import importlib
    from training.reference_evaluation import build_training_reference
    if name == "db_context_fit":
        from test_db_context_fit import sample
        frame, features = sample()
    else:
        from test_db_shared_optimized import fixture_data
        frame, features = fixture_data()
    profile = importlib.import_module("training." + name)
    class Booster:
        def predict(self, matrix, **kwargs):
            return -matrix.safe_horse_number.to_numpy(dtype=float)
    monkeypatch.setattr(profile, "_train_booster", lambda *args, **kwargs: Booster())
    bundle, result = profile.fit_shared_model(frame, features, n_threads=1)
    bundle["fit_source_sha256"] = hashlib.sha256(Path(profile.__file__).read_bytes()).hexdigest()
    bundle.setdefault("feature_source_hashes", {"test": "a" * 64})
    report = build_training_reference(bundle, frame, features, result["partitions"]["TEST"], "model")
    assert len(report["records"]) == result["partitions"]["TEST"]["race_count"]
    assert {payload["race_info"]["date"] for payload in report["records"].values()}.isdisjoint(
        frame.loc[frame.race_date <= result["partitions"]["CAL"]["date_to"], "race_date"])
    assert all(abs(sum(row["p_norm"] for row in payload["predictions"]) - 1) < 1e-8
               for payload in report["records"].values())
