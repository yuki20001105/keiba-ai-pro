import pytest


def test_untrusted_reference_bytes_are_rejected_before_unpickling(tmp_path, monkeypatch):
    from services import monthly_reference_input as reference

    path = tmp_path / "model_monthly_reference_random_20261105.joblib"
    path.write_bytes(b"not an admitted model")
    monkeypatch.setattr(reference.joblib, "load", lambda *_: pytest.fail("untrusted pickle opened"))
    with pytest.raises(ValueError, match="artifact"):
        reference.load_published_bundle(path)


def test_reference_inventory_requires_publication_not_only_research_artifacts(tmp_path, monkeypatch):
    from services import monthly_reference_input as reference

    monkeypatch.setattr(reference, "MODELS_DIR", tmp_path)
    assert reference.list_reference_models() == []


def test_cpu_worker_does_not_fall_through_to_unsigned_legacy_loader(tmp_path, monkeypatch):
    from services.prediction_compute import _load_bundle
    import joblib

    path = tmp_path / "model_monthly_reference_random_20261105.joblib"
    path.write_bytes(b"not an admitted model")
    monkeypatch.setattr(joblib, "load", lambda *_: pytest.fail("worker opened untrusted pickle"))
    with pytest.raises(ValueError, match="monthly-reference-untrusted-artifact"):
        _load_bundle(str(path))


def test_saved_reference_loader_keeps_purchase_authority_disabled():
    from services import monthly_reference_input as reference

    spec = reference.specs()[0]
    source = reference.STUDY_ROOT / spec["directory"] / "model.joblib"
    if not source.is_file():
        pytest.skip("sealed study is local-only")
    bundle = reference.load_source_bundle(spec)
    assert bundle["reference_only"] is True
    assert bundle["probability_validation"]["eligible_for_ev"] is False
    assert bundle["probability_validation"]["validated"] is False
    membership = bundle["_monthly_reference"]
    assert not set(membership["test_dates"]) & membership["development_races"]
    assert len(membership["test_dates"]) == 6502
    assert bundle["training_date_to"] == "20261017"
    assert min(membership["test_dates"].values()) < "2026-10-17"


@pytest.mark.parametrize("index", [0, 1])
def test_import_features_keep_fitted_order_and_exact_test_identity(index):
    import runpy
    from pathlib import Path
    import pandas as pd
    from services import monthly_reference_input as reference

    spec = reference.specs()[index]
    if not (reference.STUDY_ROOT / "study-summary.json").is_file():
        pytest.skip("sealed study is local-only")
    functions = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts" / "publish_monthly_reference.py"))
    bundle = reference.load_source_bundle(spec)
    target, _ = functions["_features"](bundle, spec)
    predictions = pd.read_parquet(reference.verified_file(Path(spec["directory"]) / "predictions.parquet"))
    assert target.loc[predictions.index, ["race_id", "horse_number"]].equals(predictions[["race_id", "horse_number"]])
