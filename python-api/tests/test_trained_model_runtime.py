"""A published model must execute its training formula, not today's parser."""
from copy import deepcopy
import hashlib
from pathlib import Path
import subprocess

import pandas as pd
import pytest


@pytest.fixture
def training_repository(tmp_path):
    from training import db_shared_fit as legacy, db_shared_optimized as optimized

    modules = [
        "experiments.dataset", "experiments.features", "experiments.search_features",
        "experiments.training", "forward_adapters.feature_state", "training.eligibility",
        "training.db_shared_features", "training.db_shared_fit",
        "training.db_shared_optimized", "training.db_shared_profiles",
        "keiba_ai.speed_deviation",
    ]
    sources = {}
    for name in modules:
        package = "keiba" if name.startswith("keiba_ai.") else "python-api"
        path = tmp_path / package / Path(*name.split(".")).with_suffix(".py")
        path.parent.mkdir(parents=True, exist_ok=True)
        body = (
            f"# {name}\n"
            "def race_class(row):\n"
            "    return row.get('race_class')\n"
        ).encode()
        path.write_bytes(body)
        sources[name] = hashlib.sha256(body).hexdigest()
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "core.autocrlf", "false"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.name=Fixture",
         "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Training sources"],
        check=True,
    )
    for path in tmp_path.glob("**/*.py"):
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    dataset = tmp_path / "python-api" / "experiments" / "dataset.py"
    dataset.write_bytes(b"def race_class(row):\r\n    return 'new inferred class'\r\n")
    bundle = {
        "schema": "local-db-shared-optimized-model-v1",
        "feature_pipeline": optimized.FEATURE_PIPELINE,
        "feature_profile": optimized.PROFILE_ID,
        "feature_columns": list(optimized.FEATURE_COLUMNS),
        "feature_source_hashes": {
            name: digest for name, digest in sources.items()
            if name not in {"keiba_ai.speed_deviation", "training.db_shared_fit"}
        },
        "source_hashes": sources,
        "fit_source_sha256": sources["training.db_shared_optimized"],
        "model_input_contract": {
            "version": optimized.INPUT_CONTRACT_VERSION,
            "feature_pipeline": optimized.FEATURE_PIPELINE,
            "feature_profile": optimized.PROFILE_ID,
            "feature_columns": list(optimized.FEATURE_COLUMNS),
            "categorical_columns": list(optimized.CATEGORICAL_COLUMNS),
            "raw_feature_pipeline": legacy.FEATURE_PIPELINE,
            "raw_feature_columns": list(legacy.FEATURE_COLUMNS),
            "excluded_features": list(optimized.EXCLUDED_FEATURES),
            "source_hashes": sources.copy(),
        },
    }
    return tmp_path, bundle, dataset


def test_original_training_formula_survives_parser_edits_and_windows_newlines(training_repository):
    from services.trained_model_runtime import recover_training_sources

    root, bundle, dataset = training_repository
    before = dataset.read_bytes()
    recovered = recover_training_sources(bundle, repository_root=root)
    namespace = {}
    exec(recovered["experiments.dataset"], namespace)
    assert namespace["race_class"]({"race_name": "new inferred class"}) is None
    assert dataset.read_bytes() == before
    assert all(hashlib.sha256(body).hexdigest() == bundle["source_hashes"][name]
               for name, body in recovered.items())


@pytest.mark.parametrize("problem", ["missing_source", "wrong_hash", "nested_contract", "wrong_columns"])
def test_unproven_training_contract_is_not_relabelled_as_compatible(training_repository, problem):
    from services.trained_model_runtime import recover_training_sources

    root, original, _ = training_repository
    bundle = deepcopy(original)
    if problem == "missing_source":
        del bundle["source_hashes"]["keiba_ai.speed_deviation"]
    elif problem == "wrong_hash":
        bundle["source_hashes"]["experiments.dataset"] = "0" * 64
        bundle["feature_source_hashes"]["experiments.dataset"] = "0" * 64
        bundle["model_input_contract"]["source_hashes"]["experiments.dataset"] = "0" * 64
    elif problem == "nested_contract":
        bundle["model_input_contract"]["source_hashes"]["experiments.dataset"] = "0" * 64
    else:
        bundle["feature_columns"].reverse()
    with pytest.raises(ValueError):
        recover_training_sources(bundle, repository_root=root)


def test_unavailable_training_sources_preserve_the_existing_api_conflict(training_repository, monkeypatch):
    from services import db_shared_input as serving, trained_model_runtime

    root, bundle, _ = training_repository
    bundle["source_hashes"]["experiments.dataset"] = "0" * 64
    bundle["feature_source_hashes"]["experiments.dataset"] = "0" * 64
    bundle["model_input_contract"]["source_hashes"]["experiments.dataset"] = "0" * 64
    monkeypatch.setattr(trained_model_runtime, "ROOT", root)
    with pytest.raises(ValueError, match="feature-contract-source-mismatch"):
        serving.verify_bundle_sources(bundle)


def test_predictor_uses_the_model_specific_matrix_and_scores(monkeypatch):
    from services import db_shared_input as serving
    from routers.predict import ModelPredictor
    from training.db_shared_profiles import OPTIMIZED_PIPELINE

    matrix = pd.DataFrame({"age": [2.0, 3.0]})
    result = {"raw": [0.3, 0.1], "probabilities": [0.6, 0.4], "calibrator_applied": True}
    monkeypatch.setattr(serving, "needs_trained_runtime", lambda bundle: True)
    monkeypatch.setattr(
        serving, "build_trained_shared_input",
        lambda *args, **kwargs: (matrix, {"source_hash_matches": True}, result),
    )
    predictor = ModelPredictor({"model": object(), "feature_pipeline": OPTIMIZED_PIPELINE})
    features = predictor.build_features(pd.DataFrame({"finish_position": [1, 2]}))
    raw, probabilities = predictor.predict_scores(features)
    pd.testing.assert_frame_equal(features, matrix)
    assert list(raw) == [0.3, 0.1]
    assert list(probabilities) == [0.6, 0.4]
    assert predictor.calibrator_applied is True


def test_trained_scores_cannot_be_reused_for_a_modified_matrix(monkeypatch):
    from services import db_shared_input as serving
    from routers.predict import ModelPredictor
    from training.db_shared_profiles import OPTIMIZED_PIPELINE

    matrix = pd.DataFrame({"age": [2.0, 3.0]})
    result = {"raw": [0.3, 0.1], "probabilities": [0.6, 0.4], "calibrator_applied": True}
    monkeypatch.setattr(serving, "needs_trained_runtime", lambda bundle: True)
    monkeypatch.setattr(
        serving, "build_trained_shared_input",
        lambda *args, **kwargs: (matrix, {"source_hash_matches": True}, result),
    )
    predictor = ModelPredictor({"model": object(), "feature_pipeline": OPTIMIZED_PIPELINE})
    features = predictor.build_features(pd.DataFrame({"horse_number": [1, 2]}))
    features.loc[0, "age"] = 99.0
    with pytest.raises(ValueError, match="matrix"):
        predictor.predict_scores(features)
