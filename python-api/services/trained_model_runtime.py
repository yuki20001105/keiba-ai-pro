"""Execute published shared models with their exact, locally verified sources."""
from __future__ import annotations

from functools import lru_cache
import hashlib
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
_RAW_MODULES = frozenset({
    "experiments.dataset", "experiments.features", "experiments.search_features",
    "experiments.training", "forward_adapters.feature_state", "training.eligibility",
    "training.db_shared_features",
})
_FIT_MODULES = _RAW_MODULES | {"keiba_ai.speed_deviation", "training.db_shared_fit"}
_OPTIMIZED_MODULES = _FIT_MODULES | {"training.db_shared_optimized", "training.db_shared_profiles"}


def _source_path(module: str) -> Path:
    package = "keiba" if module.startswith("keiba_ai.") else "python-api"
    return Path(package) / Path(*module.split(".")).with_suffix(".py")


@lru_cache(maxsize=64)
def _recover_source(root: str, module: str, expected: str) -> bytes:
    relative = _source_path(module)
    try:
        current = (Path(root) / relative).read_bytes()
        for body in (current, current.replace(b"\r\n", b"\n")):
            if hashlib.sha256(body).hexdigest() == expected:
                return body
    except OSError:
        pass
    try:
        revisions = subprocess.run(
            ["git", "--no-pager", "log", "-32", "--format=%H", "--", relative.as_posix()],
            cwd=root, check=True, capture_output=True, timeout=10,
        ).stdout.decode("ascii").splitlines()
        for revision in ["HEAD", *revisions]:
            result = subprocess.run(
                ["git", "--no-pager", "show", f"{revision}:{relative.as_posix()}"],
                cwd=root, capture_output=True, timeout=10,
            )
            if result.returncode == 0 and hashlib.sha256(result.stdout).hexdigest() == expected:
                return result.stdout
    except (OSError, subprocess.SubprocessError, UnicodeError):
        pass
    raise ValueError(f"shared-db-trained-source-unavailable:{module}")


def recover_training_sources(bundle: dict, *, repository_root: Path | None = None) -> dict[str, bytes]:
    """Never accept edited formulas by changing or normalizing the model's pins."""
    from training import db_shared_fit as legacy, db_shared_optimized as optimized

    pipeline = bundle.get("feature_pipeline")
    if pipeline == legacy.FEATURE_PIPELINE:
        profile, required, feature_modules = legacy, _FIT_MODULES, _RAW_MODULES
        fit_module = "training.db_shared_fit"
    elif pipeline == optimized.FEATURE_PIPELINE:
        profile, required = optimized, _OPTIMIZED_MODULES
        feature_modules = required - {"keiba_ai.speed_deviation", "training.db_shared_fit"}
        fit_module = "training.db_shared_optimized"
    else:
        raise ValueError("shared-db-trained-runtime-pipeline-unsupported")
    sources = bundle.get("source_hashes")
    contract = bundle.get("model_input_contract", {})
    if (not isinstance(sources, dict) or set(sources) != required
            or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                   for value in sources.values())
            or bundle.get("feature_columns") != list(profile.FEATURE_COLUMNS)
            or contract.get("version") != profile.INPUT_CONTRACT_VERSION
            or contract.get("source_hashes") != sources
            or bundle.get("feature_source_hashes") != {name: sources[name] for name in feature_modules}
            or bundle.get("fit_source_sha256") != sources[fit_module]):
        raise ValueError("shared-db-trained-runtime-contract-mismatch")
    if pipeline == optimized.FEATURE_PIPELINE and (
        bundle.get("schema") != "local-db-shared-optimized-model-v1"
        or bundle.get("feature_profile") != optimized.PROFILE_ID
        or contract.get("feature_pipeline") != pipeline
        or contract.get("feature_profile") != optimized.PROFILE_ID
        or contract.get("feature_columns") != list(profile.FEATURE_COLUMNS)
        or contract.get("categorical_columns") != list(profile.CATEGORICAL_COLUMNS)
        or contract.get("raw_feature_pipeline") != legacy.FEATURE_PIPELINE
        or contract.get("raw_feature_columns") != list(legacy.FEATURE_COLUMNS)
        or contract.get("excluded_features") != list(optimized.EXCLUDED_FEATURES)
    ):
        raise ValueError("shared-db-trained-runtime-contract-mismatch")
    root = str((repository_root or ROOT).resolve())
    return {name: _recover_source(root, name, sources[name]) for name in sorted(required)}


def run_trained_prediction(bundle, horses, *, db_path, race_info, model_path, n_threads=2):
    """A fresh process keeps archived imports out of the API and reused workers."""
    import joblib
    from services.db_shared_input import validate_published_artifact

    path = Path(model_path)
    digest = validate_published_artifact(path)
    path = path.resolve(strict=True)
    sources = recover_training_sources(bundle)
    with tempfile.TemporaryDirectory(prefix="keiba-trained-input-") as directory:
        snapshot = Path(directory)
        packages = set()
        for name, body in sources.items():
            relative = _source_path(name)
            target = snapshot / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            packages.add(relative.parent)
        # Only pinned modules are overridden. Existing package helpers remain
        # available, without replacing any imported modules in the API.
        for package in packages:
            (snapshot / package / "__init__.py").write_text(
                f"__path__.append({str(ROOT / package)!r})\n", encoding="utf-8",
            )
        request, response = snapshot / "request.joblib", snapshot / "response.joblib"
        joblib.dump({
            "root": str(ROOT), "snapshot": str(snapshot), "model_path": str(path),
            "artifact_sha256": digest, "source_hashes": bundle["source_hashes"],
            "db_path": str(Path(db_path).resolve()), "race_info": race_info,
            "horses": horses, "n_threads": n_threads,
        }, request)
        try:
            completed = subprocess.run(
                [sys.executable, "-I", str(Path(__file__).resolve()), str(request), str(response)],
                capture_output=True, timeout=300,
            )
        except subprocess.TimeoutExpired as error:
            raise ValueError("shared-db-trained-runtime-timeout") from error
        if completed.returncode != 0 or not response.is_file():
            raise ValueError("shared-db-trained-runtime-failed")
        result = joblib.load(response)
    if "error" in result:
        raise ValueError(result["error"])
    if (validate_published_artifact(path) != digest
            or result.get("source_hashes") != bundle["source_hashes"]
            or list(result["features"]) != bundle["feature_columns"]
            or len(result["features"]) != len(horses)):
        raise ValueError("shared-db-trained-runtime-result-mismatch")
    return result


def _worker(request_path: str, response_path: str) -> None:
    import joblib

    request = joblib.load(request_path)
    root, snapshot = Path(request["root"]), Path(request["snapshot"])
    sys.path[:0] = [
        str(snapshot / "python-api"), str(snapshot / "keiba"),
        str(root / "python-api"), str(root / "keiba"),
    ]
    from services.prediction_compute import _configure_worker_logging
    _configure_worker_logging()
    from services.db_shared_input import validate_published_artifact
    import numpy as np
    from training import db_shared_features, db_shared_fit, db_shared_optimized
    from experiments.dataset import _date
    from threadpoolctl import threadpool_limits

    path = Path(request["model_path"])
    try:
        digest = validate_published_artifact(path)
        if digest != request["artifact_sha256"]:
            raise ValueError("shared-db-artifact-changed-before-trained-runtime")
        bundle = joblib.load(path)
        if validate_published_artifact(path) != digest:
            raise ValueError("shared-db-artifact-changed-during-trained-runtime")
        profile = (db_shared_optimized if bundle["feature_pipeline"] == db_shared_optimized.FEATURE_PIPELINE
                   else db_shared_fit)
        profile._validate_prediction_bundle(bundle)
        if profile.source_hashes() != request["source_hashes"]:
            raise ValueError("shared-db-trained-runtime-source-mismatch")
        with threadpool_limits(limits=request["n_threads"]):
            raw_features, audit = db_shared_features.build_serving_features(
                request["db_path"], request["race_info"], request["horses"],
            )
            if profile is db_shared_optimized:
                raw_features = profile.select_features(raw_features)
            features = bundle["encoder"].transform(raw_features)
            raw, probabilities = profile.predict_encoded_shared_scores(
                bundle, features, n_threads=request["n_threads"],
            )
            if probabilities is None:
                weights = np.exp(raw - raw.max())
                probabilities = weights / weights.sum()
        info = request["race_info"]
        result = {
            "raw_features": raw_features, "features": features, "audit": audit,
            "raw": raw, "probabilities": probabilities, "source_hashes": profile.source_hashes(),
            "race_info": {**info, "date": _date(info.get("date") or info.get("race_date"))},
            "calibrator_applied": bool(bundle.get("probability_validation", {}).get("validated") is True
                                       and bundle.get("calibrator") is not None),
        }
    except Exception as error:
        result = {"error": str(error)}
    joblib.dump(result, response_path)


if __name__ == "__main__":
    _worker(sys.argv[1], sys.argv[2])
