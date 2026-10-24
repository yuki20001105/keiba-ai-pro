"""Read-only evidence for the active local model's input contract.

This is an audit, not an input repair or a model evaluation. It never trains,
refreshes HTTP data, changes the active model, or writes to SQLite. A new JSON
report is its only output file and existing reports are never overwritten.
Matching names/order, finite predictions, and gain fractions cannot establish
semantic training/inference parity. The conclusion is deliberately fixed to
NOT_SEMANTICALLY_VERIFIED, including when the numerical smoke test succeeds.

Run from the repository root with python-api/.venv/Scripts/python.exe:
  scripts/audit_active_model_input_compatibility.py --check-point-in-time
"""
from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager, redirect_stdout
import copy
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import io
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
import warnings

ROOT = Path(__file__).resolve().parents[1]
CONCLUSION = "NOT_SEMANTICALLY_VERIFIED"
SCHEMA = "active-model-input-compatibility-audit-v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def classify_columns(columns: list[str]) -> dict:
    """Classify observed names; never synthesize a value for a missing name."""
    groups = {
        "legacy_previous_3_to_5_raw": [],
        "legacy_external_holding_time": [],
        "race_day_observation": [],
        "other": [],
    }
    for name in columns:
        if re.fullmatch(r"prev[345]_race_(finish|time|weight|distance)", name):
            group = "legacy_previous_3_to_5_raw"
        elif re.fullmatch(r"holding_(just|middle|short|long)_(time_sec|l3f|finish|babasa)", name):
            group = "legacy_external_holding_time"
        elif name == "horse_weight_change":
            group = "race_day_observation"
        else:
            group = "other"
        groups[group].append(name)
    return groups


def immutable_report(path: Path, report: dict) -> None:
    # Exclusive create is essential: this script must not replace evidence.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


@contextmanager
def readonly_sqlite():
    """Force the existing SELECT-only loader's connections into SQLite RO."""
    original = sqlite3.connect

    def connect(database, *args, **kwargs):
        text = str(database)
        if text.startswith("file:"):
            if "mode=ro" not in text:
                raise ValueError("Audit only permits read-only SQLite URIs")
            kwargs["uri"] = True
            return original(database, *args, **kwargs)
        path = Path(database).resolve()
        kwargs["uri"] = True
        return original(path.as_uri() + "?mode=ro", *args, **kwargs)

    sqlite3.connect = connect
    try:
        yield
    finally:
        sqlite3.connect = original


def source_at(commit: str, path: str) -> bytes | None:
    if not re.fullmatch(r"[0-9a-f]{7,40}", commit):
        return None
    result = subprocess.run(
        ["git", "show", f"{commit}:{path}"], cwd=ROOT,
        check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return result.stdout.replace(b"\r\n", b"\n") if result.returncode == 0 else None


def pickle_sklearn_version(path: Path) -> list[str]:
    # Read serialized metadata without resaving the bundle or assuming that a
    # newly unpickled object's __getstate__ reflects its saved version.
    from joblib.numpy_pickle_utils import _validate_fileobject_and_memmap
    with path.open("rb") as raw:
        with _validate_fileobject_and_memmap(raw, str(path), mmap_mode=None) as (stream, _):
            serialized = stream.read()
    versions = set()
    for match in re.finditer(b"_sklearn_version", serialized):
        chunk = serialized[match.end():match.end() + 48]
        found = re.search(rb"\d+\.\d+(?:\.\d+)?(?:[a-z0-9.+-]*)", chunk)
        if found:
            versions.add(found.group().decode("ascii"))
    return sorted(versions)


def source_dropper(pd, future_fields):
    """Execute only the actual API's pure filter, not its import side effects."""
    path = ROOT / "python-api/routers/predict.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_drop_non_features"
    )
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    namespace = {"pd": pd, "POST_RACE_FIELDS": future_fields}
    exec(compile(module, str(path), "exec"), namespace)
    return namespace["_drop_non_features"]


def build_input(bundle, frame, history, dropper):
    """Mirror the current build_features stages without HTTP/log/DB writes.

    No exception fallback or input invention is used. Restoration is restricted
    to numeric/bool columns already computed by the current FE, as in the API.
    """
    import pandas as pd
    from keiba_ai.feature_engineering import add_derived_features
    engineered = add_derived_features(frame, full_history_df=history)
    engineered = engineered.loc[:, ~engineered.columns.duplicated()]
    optimizer = copy.deepcopy(bundle.get("optimizer"))
    with redirect_stdout(io.StringIO()):
        transformed = optimizer.transform(engineered) if optimizer is not None else engineered.copy()
    optimizer_lost = [
        name for name in bundle["feature_columns"]
        if name in engineered and name not in transformed
    ]
    restored = []
    for name in bundle["feature_columns"]:
        if name in transformed or name not in engineered:
            continue
        values = engineered[name]
        if pd.api.types.is_numeric_dtype(values) or pd.api.types.is_bool_dtype(values):
            transformed[name] = pd.to_numeric(values, errors="coerce")
            restored.append(name)
    filtered = dropper(transformed)
    missing = [name for name in bundle["feature_columns"] if name not in filtered]
    # This is the existing API's NaN reindex policy, not a proposed repair.
    aligned = filtered.reindex(columns=bundle["feature_columns"])
    return engineered, aligned, missing, optimizer_lost, restored


def parse_dates(values):
    import pandas as pd
    return pd.to_datetime(values.astype("string"), format="mixed", errors="coerce").dt.normalize()


def audit_sample(bundle, db: Path, race_id: str, dropper, check_pit: bool) -> dict:
    import numpy as np
    import pandas as pd
    from benchmark_prediction_parallel import input_frame
    from keiba_ai.db_ultimate_loader import load_prediction_history_frame
    started = time.perf_counter()
    with readonly_sqlite():
        frame = input_frame(db, race_id)
        stored_history = load_prediction_history_frame(db, frame, excluded_race_ids={race_id})
    target_dates = parse_dates(frame["race_date"]).dropna().unique()
    if len(target_dates) != 1:
        raise ValueError(f"Target race does not have exactly one proven date: {race_id}")
    target_date = pd.Timestamp(target_dates[0])
    dates = parse_dates(stored_history["race_date"])
    prior = dates < target_date
    historical = pd.concat([stored_history, frame], ignore_index=True)
    engineered, aligned, missing, lost, restored = build_input(bundle, frame, historical, dropper)
    scores = np.asarray(bundle["model"].predict(aligned, num_threads=2), dtype=float)
    gain = dict(zip(bundle["model"].feature_name(), bundle["model"].feature_importance("gain")))
    total_gain = float(sum(gain.values()))
    categories = {}
    for name in bundle.get("categorical_features", []):
        categories[name] = {
            "dtype": str(aligned[name].dtype),
            "nonnull": int(aligned[name].notna().sum()),
            "distinct_values": [float(value) for value in aligned[name].dropna().unique()],
        }
    pit = {
        "constraint": "history_race_date < target_race_date; same-day outcomes excluded",
        "target_date": target_date.strftime("%Y-%m-%d"),
        "stored_history_rows": len(stored_history),
        "strictly_prior_rows": int(prior.sum()),
        "same_day_rows": int((dates == target_date).sum()),
        "future_rows": int((dates > target_date).sum()),
        "unknown_date_rows": int(dates.isna().sum()),
        "source_snapshot_timing_verified": False,
        "scope_warning": "A history boundary test cannot certify legacy profile or external holding-time snapshot timing.",
    }
    if check_pit:
        strictly_prior = pd.concat([stored_history.loc[prior], frame], ignore_index=True)
        _, restricted, _, _, _ = build_input(bundle, frame, strictly_prior, dropper)
        changed = []
        for name in aligned:
            try:
                pd.testing.assert_series_equal(aligned[name], restricted[name], check_dtype=False, check_exact=True)
            except AssertionError:
                changed.append(name)
        restricted_scores = np.asarray(bundle["model"].predict(restricted, num_threads=2), dtype=float)
        pit.update({
            "boundary_invariance_test": "PASS" if not changed and np.array_equal(scores, restricted_scores) else "FAIL",
            "changed_features_after_future_and_same_day_removal": changed,
            "scores_exact_equal": bool(np.array_equal(scores, restricted_scores)),
            "maximum_absolute_score_change": float(np.max(np.abs(scores - restricted_scores))),
            "production_input_changed": False,
        })
    else:
        pit["boundary_invariance_test"] = "NOT_RUN"
    previous_dates = {}
    for prefix in ("prev", "prev2", "prev3", "prev4", "prev5"):
        key = f"{prefix}_race_date"
        if key not in frame:
            previous_dates[prefix] = {"status": "DATE_COLUMN_ABSENT"}
            continue
        observed = parse_dates(frame[key])
        previous_dates[prefix] = {
            "known_dates": int(observed.notna().sum()),
            "strictly_prior": int((observed < target_date).sum()),
            "same_day": int((observed == target_date).sum()),
            "future": int((observed > target_date).sum()),
            "unknown": int(observed.isna().sum()),
        }
    venue_values = engineered["venue_code"].dropna().astype(str).unique().tolist()
    return {
        "race_id": race_id, "rows": len(frame), "elapsed_seconds": time.perf_counter() - started,
        "raw_columns_sha256": hashlib.sha256(json.dumps(sorted(frame.columns)).encode()).hexdigest(),
        "uncomputed_columns": missing, "uncomputed_count": len(missing),
        "uncomputed_fraction": len(missing) / len(bundle["feature_columns"]),
        "uncomputed_groups": classify_columns(missing),
        "required_columns_lost_by_optimizer": lost, "computed_numeric_columns_restored": restored,
        "all_nan_columns": [name for name in aligned if aligned[name].isna().all()],
        "missing_value_rate": float(aligned.isna().to_numpy().mean()),
        "category_values": categories,
        "dtype_counts": {str(name): int(value) for name, value in aligned.dtypes.astype(str).value_counts().items()},
        "uncomputed_gain_fraction": sum(float(gain[name]) for name in missing) / total_gain if total_gain else None,
        "gain_interpretation": "Training split gain share only; NOT accuracy loss, NOT expected error, NOT evidence of harmless missingness.",
        "finite_score_smoke_test": bool(np.isfinite(scores).all()),
        "scores_sha256": hashlib.sha256(scores.astype("<f8").tobytes()).hexdigest(),
        "score_range": [float(scores.min()), float(scores.max())],
        "point_in_time": pit, "legacy_previous_run_date_checks": previous_dates,
        "provider_id_schema_diagnostic": {
            "provider": "JRA", "race_id_venue_slice_4_6": race_id[4:6],
            "current_fe_venue_code_values": venue_values,
            "current_and_training_fe_use_slice_8_10": True,
            "semantic_issue": "The legacy venue_code field is derived from the day component, not the JRA venue component.",
            "existing_model_input_must_not_be_silently_changed": True,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--race-id", action="append", default=None)
    parser.add_argument("--db", type=Path, default=ROOT / "keiba/data/keiba_ultimate.db")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/generated/release-readiness-20261010/active-model-input-audit.json")
    parser.add_argument("--check-point-in-time", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"Refusing to overwrite evidence: {args.output}")
    sys.path[:0] = [str(ROOT / "keiba"), str(ROOT / "scripts")]
    import joblib
    import pandas as pd
    # Warm imports before capturing warnings. Importing optuna_optimizer can
    # install a global ignore filter; loading after this point captures actual
    # bundle version warnings rather than confusing suppressed with absent.
    import keiba_ai.optuna_optimizer  # noqa: F401
    import keiba_ai.feature_engineering as feature_module
    import keiba_ai.lightgbm_feature_optimizer as optimizer_module
    from keiba_ai.constants import FUTURE_FIELDS, ID_COLUMNS
    active_path = ROOT / "python-api/models/.active_model.json"
    active_before = sha256_file(active_path)
    model_id = json.loads(active_path.read_text(encoding="utf-8"))["model_id"]
    model_path = active_path.parent / (model_id + ".joblib")
    model_before = sha256_file(model_path)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        bundle = joblib.load(model_path)
    feature_names = list(bundle.get("feature_columns", []))
    booster_names = list(bundle["model"].feature_name())
    source_hashes = {}
    for relative in (
        "keiba/keiba_ai/feature_engineering.py", "keiba/keiba_ai/point_in_time_history.py",
        "keiba/keiba_ai/lightgbm_feature_optimizer.py", "keiba/keiba_ai/constants.py",
        "keiba/keiba_ai/db_ultimate_loader.py", "python-api/routers/predict.py",
        "scripts/benchmark_prediction_parallel.py",
    ):
        source_hashes[relative] = sha256_file(ROOT / relative)
    import inspect
    current_hash = hashlib.md5(inspect.getsource(feature_module).encode()).hexdigest()[:12]
    trained_hash = bundle.get("pipeline_config", {}).get("feature_engineering_hash")
    commit = (bundle.get("local_execution") or {}).get("candidate_commit_sha", "")
    old_source = source_at(commit, "keiba/keiba_ai/feature_engineering.py")
    old_hash = hashlib.md5(old_source).hexdigest()[:12] if old_source is not None else None
    dropper = source_dropper(pd, FUTURE_FIELDS)
    samples = [audit_sample(bundle, args.db, race_id, dropper, args.check_point_in_time)
               for race_id in (args.race_id or ["202605040301"])]
    frozen_unchanged = active_before == sha256_file(active_path) and model_before == sha256_file(model_path)
    runtime_unchanged = all(sha256_file(ROOT / path) == value for path, value in source_hashes.items())
    encoders = {}
    for name, encoder in getattr(bundle.get("optimizer"), "label_encoders", {}).items():
        encoders[name] = {"classes_dtype": str(encoder.classes_.dtype), "classes": encoder.classes_.tolist()}
    gain = dict(zip(booster_names, bundle["model"].feature_importance("gain")))
    report = {
        "schema": SCHEMA, "observed_at": datetime.now(timezone.utc).isoformat(),
        "conclusion": CONCLUSION,
        "contract": {
            "semantic_parity_verified": False, "read_only_database": True,
            "no_http": True, "no_training": True, "no_model_activation": True,
            "no_existing_artifact_changes": True,
            "aligned_columns_and_finite_scores_do_not_certify_semantic_or_betting_safety": True,
        },
        "model": {
            "id": model_id, "path": str(model_path), "sha256": model_before,
            "active_pointer_sha256": active_before, "frozen_files_unchanged": frozen_unchanged,
            "target": bundle.get("target"), "feature_count": len(feature_names),
            "feature_columns": feature_names, "booster_feature_names": booster_names,
            "feature_order_exact_match": feature_names == booster_names,
            "future_field_intersection": sorted(set(feature_names) & FUTURE_FIELDS),
            "identifier_field_intersection": sorted(set(feature_names) & ID_COLUMNS),
            "training_feature_md5_12": trained_hash, "runtime_feature_md5_12": current_hash,
            "source_hash_matches": trained_hash == current_hash,
            "training_candidate_commit": commit, "candidate_git_source_md5_12": old_hash,
            "training_source_identified_by_exact_saved_hash": trained_hash == old_hash and old_hash is not None,
            "categorical_features": bundle.get("categorical_features"),
            "booster_pandas_categorical": bundle["model"].pandas_categorical,
            "data_count": bundle.get("data_count"), "race_count": bundle.get("race_count"),
            "saved_evaluation": bundle.get("evaluation"),
            "evaluation_caveat": "Saved legacy-model evaluation, not a fresh evaluation under the present serving input. RMSE is speed-deviation units, not raw m/s; legacy AUC/logloss names do not change its regression task.",
        },
        "runtime": {
            "versions": {name: metadata.version(name) for name in ("joblib", "numpy", "pandas", "scikit-learn", "lightgbm")},
            "saved_sklearn_versions": pickle_sklearn_version(model_path),
            "load_warnings": [{"category": item.category.__name__, "message": str(item.message)} for item in caught],
            "source_sha256": source_hashes, "source_files_unchanged_during_audit": runtime_unchanged,
            "pipeline_execution": "Current FE + a deepcopy of saved optimizer + actual API filter extracted as AST + numeric-only computed restoration + bundle-order NaN reindex; no API import or endpoint calls.",
        },
        "encoders": {
            "saved": encoders,
            "contract_issue": "Fit string-casts categories; current transform tests x in saved classes without string-casting. Numeric 5 and string '5' do not equal stored '05'. Do not refit or reorder encoders to repair an old bundle.",
        },
        "samples": samples,
        "historical_missing_count_claims": {
            "29": {"status": "REPRODUCED" if any(sample["uncomputed_count"] == 29 for sample in samples) else "NOT_REPRODUCED"},
            "41": {"status": "REPRODUCED" if any(sample["uncomputed_count"] == 41 for sample in samples) else "NOT_REPRODUCED", "reason": "No evidence-backed sample race or serving condition for the earlier count is available; not treated as an observed defect in this audit."},
        },
        "missing_feature_semantics": {
            "legacy_previous_3_to_5_raw": {
                "safe_silent_restoration": False,
                "reason": "Current FE does not generate these twelve raw columns. Date-bounded DB histories can support new explicit features, but old provider snapshot ordering, units, availability and training distribution equivalence must be established before restoring an old bundle.",
            },
            "legacy_external_holding_time": {
                "safe_silent_restoration": False,
                "generation_source": "05f6fac:python-api/scraping/race.py::_fetch_holding_times -> _apply_holding_times",
                "source_url_type": "https://race.netkeiba.com/race_api/ class=AplFreqSum method=get; holding_time.html referer",
                "source_path": "data.*freq_sum*.freq_horse_all[horse_id].best_time_detail.{just,short,middle,long}",
                "raw_mapping": {"time_sec": "time parsed as minutes:seconds or seconds, rounded to one decimal", "l3f": "float(l3f)", "finish": "int(jyuni)", "babasa": "int(babasa)"},
                "missing_policy": "Missing tab/time skipped; parse failure None; no zero-fill",
                "point_in_time_guarantee": False,
                "reason": "The old race_date parameter only selects the cache path. It does not prove the external best_time_detail excludes future results or that each legacy snapshot existed before target race. DB-best times are not a demonstrated equivalent substitute.",
            },
            "race_day_observation": {
                "safe_silent_restoration": False,
                "reason": "horse_weight_change can be copied from an actually observed pre-race weight_change with the same definition. It cannot be inferred from a later weighing or absence; this sample has neither source value.",
            },
            "computed_numeric_fields_pruned_by_optimizer": {
                "safe_structural_restoration": "ONLY_IF_ALREADY_PRESENT_NUMERIC_OR_BOOL_AND_NOT_FORBIDDEN",
                "semantic_parity_still_unverified": True,
                "reason": "Preserving a genuinely computed, name-identical value is structurally distinct from inventing absent history. It does not certify that a changed feature implementation matches its training-time definition.",
            },
        },
        "training_gain_by_feature": {name: float(value) for name, value in gain.items()},
        "gain_caveat": "Gain fractions quantify stored tree split use, never accuracy loss or expected Spearman/RMSE change.",
        "safe_migration_candidate_needed": True,
        "migration_gates": [
            "Create a separate candidate with a fixed provider-ID schema, point-in-time history definitions, versioned FE/optimizer/encoder, no external values whose timing is unproved, and an identical train/serve feature contract. Do not alter the active model or H04/Track A.",
            "Verify one-row and multi-race train/serve feature values, dtypes, encoder mapping, finite outputs, source hashes and exclusion of same-day/future outcomes. Distinguish structural missing observations from absent computation, and report all missingness groups.",
            "Compare candidate vs a frozen baseline on an untouched time cohort, mainly per-race Spearman and same-target-unit RMSE, with uncertainty and segment coverage. Separately establish probability calibration and betting eligibility; do not reuse saved legacy evaluation as present serving quality.",
        ],
    }
    immutable_report(args.output, report)
    print(json.dumps({"report": str(args.output), "conclusion": CONCLUSION,
                      "model": model_id, "samples": [{"race_id": sample["race_id"], "uncomputed_count": sample["uncomputed_count"], "pit": sample["point_in_time"]["boundary_invariance_test"]} for sample in samples]}, ensure_ascii=False))
    return 0 if frozen_unchanged and runtime_unchanged else 1


if __name__ == "__main__":
    raise SystemExit(main())
