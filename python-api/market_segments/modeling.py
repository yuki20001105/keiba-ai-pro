"""Small, fixed CPU residual comparisons; never promotes a production model.

The caller must freeze the experiment plan, feature specification and input parquet
before launching. Historical odds remain a retrospective proxy, not T-5 evidence.
Unavailable groups produce an explicit skip, not a copy of a baseline prediction.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import time

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from scipy.optimize import minimize
from scipy.special import expit
from threadpoolctl import threadpool_limits

from market_edge.modeling import FEATURES, MARKET_FEATURES, PARAMS, logistic_loss, market_offset, normalize

REPO = Path(__file__).resolve().parents[2]
GROUPS = {"A", "B", "C", "D", "E", "F", "P", "D_prior_speed_proxy"}
STABLE_FEATURES = ["safe_weight_change", "safe_prior_starts", "safe_days_since_last",
                   "safe_jockey_win_rate", "safe_trainer_win_rate", "safe_horse_surface_win_rate",
                   "safe_horse_distance_band_win_rate", "safe_horse_course_win_rate"]
FORBIDDEN = {"finish", "is_win", "time_seconds", "last_3f_time", "speed_deviation",
             "target", "label", "odds", "popularity", "tansho_payout", "payout",
             "race_id", "horse_id", "horse_number", "race_date", "phase", "prediction",
             "h04_probability_fixed_reference"}
PHASE_DATES = {"calibration_seed": ("20210101", "20210630"),
               "meta_fit": ("20210701", "20210930"),
               "development": ("20211001", "20211231"),
               "validation": ("20220101", "20221231")}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)


def _verified_json(path, expected, name):
    if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise ValueError(f"Missing or invalid {name} SHA256")
    if sha256(path) != expected:
        raise ValueError(f"{name} hash mismatch")
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _quality_gate(gate):
    quality = _verified_json(gate.get("quality_summary_path"), gate.get("quality_summary_sha256"), "Phase 1 quality gate")
    if quality.get("MODEL_RESEARCH_READY") is not True:
        raise ValueError("Phase 1 quality gate is not PASS; Phase 2 is prohibited")
    manifest = _verified_json(gate.get("quality_manifest_path"), gate.get("quality_manifest_sha256"), "quality manifest")
    quality_root = Path(gate["quality_manifest_path"]).resolve().parent
    if Path(gate["quality_summary_path"]).resolve() != quality_root / "quality_summary.json":
        raise ValueError("Quality summary/manifest root mismatch")
    files = manifest.get("files", {})
    if files.get("quality_summary.json") != gate["quality_summary_sha256"]:
        raise ValueError("Quality manifest does not bind PASS summary")
    for section, directory in (("files", quality_root), ("code", REPO)):
        for name, digest in manifest.get(section, {}).items():
            relative = Path(name.replace("\\", "/"))
            target = directory / relative
            if relative.is_absolute() or ".." in relative.parts or not target.resolve().is_relative_to(directory) or target.is_symlink():
                raise ValueError("Unsafe quality manifest path")
            if sha256(target) != digest:
                raise ValueError("Quality manifest artifact/source hash mismatch")
    release = _verified_json(gate.get("quality_release_path"), gate.get("quality_release_sha256"), "quality release")
    if (release.get("schema") != "market-h04-quality-release-v1" or release.get("released") is not True
            or Path(release.get("quality_root", "")).resolve() != quality_root
            or release.get("quality_summary_sha256") != gate["quality_summary_sha256"]
            or release.get("quality_manifest_sha256") != gate["quality_manifest_sha256"]):
        raise ValueError("Independent Phase 1 quality release is absent or inconsistent")


def _contract(data_path, spec_path, plan_path, plan_sha, spec_sha):
    plan = _verified_json(plan_path, plan_sha, "plan")
    gate = plan.get("phase1_gate", {})
    _quality_gate(gate)
    if plan.get("feature_spec_sha256") != spec_sha:
        raise ValueError("Plan does not bind feature specification")
    spec = _verified_json(spec_path, spec_sha, "feature specification")
    dataset_sha = spec.get("dataset_sha256")
    planned_sha = plan.get("dataset_sha256", plan.get("data", {}).get("dataset_sha256"))
    if not dataset_sha or dataset_sha != planned_sha or sha256(data_path) != dataset_sha:
        raise ValueError("Dataset hash mismatch or unbound dataset")
    groups = spec.get("groups", {})
    if not groups or set(groups) - GROUPS:
        raise ValueError("Unknown or missing predeclared feature groups")
    available = [g for g, item in groups.items() if g != "A" and item.get("status") == "available"]
    if len(available) > 5:
        raise ValueError("Predeclared fit budget exceeds 20 fits")
    return spec, {"plan_sha256": plan_sha, "feature_spec_sha256": spec_sha,
                  "dataset_sha256": dataset_sha, "maximum_planned_fits": len(available) * 4,
                  "phase1_quality_summary_sha256": gate["quality_summary_sha256"],
                  "phase1_quality_manifest_sha256": gate["quality_manifest_sha256"],
                  "phase1_quality_release_sha256": gate["quality_release_sha256"]}


def _features(spec, group):
    definition = spec["groups"].get(group)
    if definition is None:
        raise ValueError("Group is not predeclared in feature specification")
    if definition.get("status") != "available":
        return None, definition.get("reason") or "Feature group is unavailable"
    if group == "A":
        return [], None
    columns = definition.get("columns")
    if not isinstance(columns, list) or not columns or len(columns) != len(set(columns)):
        raise ValueError("Invalid fixed feature columns")
    base = MARKET_FEATURES if group == "F" else FEATURES
    if columns[:len(base)] != base or (group == "B" and columns != FEATURES):
        raise ValueError("Base feature parity or fixed B specification violated")
    extra = columns[len(base):]
    if group != "B" and not extra:
        return None, "No additional features; baseline duplication is prohibited"
    if group == "F" and extra != STABLE_FEATURES:
        raise ValueError("Stable feature group must equal the eight predeclared event-time features")
    if group in {"P", "D_prior_speed_proxy"} and len(extra) > 4:
        raise ValueError("Prior-speed proxy exceeds four additional columns")
    evidence = spec.get("feature_evidence", {})
    from experiments.features import ALLOWED_FEATURES
    from experiments.search_features import SEARCH_FEATURES
    approved_safe = ALLOWED_FEATURES | set(SEARCH_FEATURES)
    for column in columns:
        if not isinstance(column, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", column):
            raise ValueError("Invalid feature name")
        if column in FORBIDDEN or column.startswith(("current_result_", "target_", "label_")) or column.endswith("_fixed_reference"):
            raise ValueError("Forbidden result/identifier/raw-market feature")
    for column in extra:
        if column not in approved_safe:
            raise ValueError("Additional feature is not a frozen safe event-time feature")
        entry = evidence.get(column, {})
        if entry.get("asof_verified") is not True or not entry.get("source_policy"):
            return None, f"No point-in-time evidence for {column}"
    if extra and definition.get("asof_verified") is not True:
        return None, "Feature group lacks point-in-time evidence"
    return columns, None


def _modeling_view(frame):
    """Never fit on H1 calibration labels, including when supplied in the parquet."""
    if not {"phase", "race_date", "race_id", "horse_number"} <= set(frame):
        raise ValueError("Missing chronological cohort columns")
    dates = frame.race_date.astype(str)
    if not dates.str.fullmatch(r"\d{8}").all() or not frame.phase.isin(PHASE_DATES).all():
        raise ValueError("Unknown chronological phase/date")
    pd.to_datetime(dates, format="%Y%m%d", errors="raise")
    for phase, (start, end) in PHASE_DATES.items():
        if not dates[frame.phase.eq(phase)].between(start, end).all():
            raise ValueError("Frozen phase/date boundary violated")
    if frame.duplicated(["race_id", "horse_number"]).any() or frame.groupby("race_id").phase.nunique().gt(1).any():
        raise ValueError("Race/runner reused across phases")
    return frame.loc[~frame.phase.eq("calibration_seed")].reset_index(drop=True)


def _validate_cohort(frame):
    required = {"race_id", "horse_number", "race_date", "phase", "expected_runner_count", "finish",
                "fold_year", "fold_train_date_to", *FEATURES}
    if required - set(frame):
        raise ValueError("Missing cohort columns: " + ",".join(sorted(required - set(frame))))
    if frame.empty or frame[list(required)].isna().any().any():
        raise ValueError("Missing baseline or cohort fields")
    dates = frame.race_date.astype(str)
    if not dates.str.fullmatch(r"\d{8}").all():
        raise ValueError("Invalid canonical race date")
    pd.to_datetime(dates, format="%Y%m%d", errors="raise")
    if not frame.phase.isin(PHASE_DATES).all():
        raise ValueError("Unknown phase; calibration and later years cannot enter model fitting")
    for phase, (start, end) in PHASE_DATES.items():
        selected = dates[frame.phase.eq(phase)]
        if not selected.between(start, end).all():
            raise ValueError("Frozen phase/date boundary violated")
    if frame.duplicated(["race_id", "horse_number"]).any():
        raise ValueError("Duplicate race/horse key")
    numeric = frame[FEATURES + ["expected_runner_count", "horse_number", "finish"]].to_numpy(float)
    if not np.isfinite(numeric).all():
        raise ValueError("Nonfinite baseline/cohort fields")
    if ((frame.finish <= 0) | (frame.finish % 1 != 0) | (frame.horse_number <= 0) |
            (frame.horse_number > 18) | (frame.horse_number % 1 != 0) |
            (frame.finish > frame.expected_runner_count)).any():
        raise ValueError("Invalid horse number or finish")
    grouped = frame.groupby("race_id", sort=False)
    if (grouped.size() != grouped.expected_runner_count.first()).any():
        raise ValueError("Incomplete race cohort")
    for column in ["race_date", "phase", "expected_runner_count", "field_size"]:
        if grouped[column].nunique().ne(1).any():
            raise ValueError("Inconsistent race metadata")
    if (grouped.size() != grouped.field_size.first()).any() or not grouped.size().between(2, 18).all():
        raise ValueError("Invalid field size")
    if frame.finish.eq(1).groupby(frame.race_id).sum().ne(1).any():
        raise ValueError("Require exactly one known winner per complete race")
    market_offset(frame)
    sums = grouped.normalized_market_probability.sum()
    if not np.allclose(sums, 1, atol=1e-10, rtol=0):
        raise ValueError("Market probabilities must already sum to one per race")
    train_dates = frame.fold_train_date_to.astype(str)
    years = dates.str[:4].astype(int)
    if (not years.eq(frame.fold_year).all()
            or not train_dates.eq(years.map({2021: "20201231", 2022: "20211231"})).all()):
        raise ValueError("H04 out-of-time boundary violated")


def _cohort_hash(frame):
    keys = frame[["race_date", "race_id", "horse_number"]].astype(str).sort_values(
        ["race_date", "race_id", "horse_number"])
    return hashlib.sha256(keys.to_csv(index=False, lineterminator="\n").encode()).hexdigest()


def _fit_preprocess(train, columns, base):
    missing = [c for c in columns if c not in train]
    if missing:
        return None, "Unavailable source columns: " + ",".join(missing)
    values = train[columns].to_numpy(float)
    if np.isinf(values).any():
        raise ValueError("Infinite feature values")
    if np.isnan(values[:, :len(base)]).any():
        raise ValueError("Missing baseline features cannot be imputed")
    extra = columns[len(base):]
    always_missing = [c for c in extra if train[c].isna().all()]
    if always_missing:
        return None, "Always missing in fit period: " + ",".join(always_missing)
    medians = np.nanmedian(values, axis=0)
    return {"columns": columns, "medians": medians.tolist(), "indicator_columns": extra,
            "matrix_columns": columns + [c + "__missing" for c in extra],
            "fit_rows": len(train), "fit_date_from": str(train.race_date.min()),
            "fit_date_to": str(train.race_date.max())}, None


def _matrix(frame, preprocessing):
    columns = preprocessing["columns"]
    values = frame[columns].to_numpy(float)
    if np.isinf(values).any():
        raise ValueError("Infinite feature values")
    mask = np.isnan(values)
    extra = preprocessing["indicator_columns"]
    base_count = len(columns) - len(extra)
    if mask[:, :base_count].any():
        raise ValueError("Missing baseline features cannot be imputed")
    if not extra:
        # Keep B's original matrix layout and arithmetic exactly unchanged.
        return values
    imputed = np.where(mask, np.asarray(preprocessing["medians"]), values)
    return np.column_stack([imputed, mask[:, base_count:].astype(float)])


def predict_model(frame, output):
    """Transform only: inference does not inspect labels, odds, payouts or phases."""
    directory = Path(output)
    artifact = json.loads((directory / "model.json").read_text(encoding="utf-8"))
    x, offset = _matrix(frame, artifact["preprocessing"]), market_offset(frame)
    if artifact["model"] == "logistic":
        x = (x - np.asarray(artifact["mean"])) / np.asarray(artifact["scale"])
        residual = np.column_stack([np.ones(len(x)), x]) @ np.asarray(artifact["beta"])
    elif artifact["model"] == "lightgbm":
        import lightgbm as lgb
        residual = lgb.Booster(model_file=str(directory / "residual.txt")).predict(x, raw_score=True, num_threads=2)
    else:
        raise ValueError("Unknown fixed model family")
    return normalize(frame, np.maximum(expit(offset + residual), np.finfo(float).tiny))


def _fit_model(train, preprocessing, model, output):
    x, offset = _matrix(train, preprocessing), market_offset(train)
    labels = train.finish.eq(1).to_numpy(float)
    weights = 1 / train.groupby("race_id").race_id.transform("size").to_numpy()
    artifact = {"model": model, "device": "cpu", "preprocessing": preprocessing,
                "features": preprocessing["matrix_columns"], "offset": "logit(normalized_market_probability)",
                "postprocess": "sigmoid(offset + residual), then normalize within each complete race"}
    start = time.perf_counter()
    if model == "logistic":
        mean, scale = x.mean(axis=0), x.std(axis=0)
        scale[scale < 1e-12] = 1
        design = np.column_stack([np.ones(len(x)), (x - mean) / scale])
        solution = minimize(logistic_loss, np.zeros(design.shape[1]),
                            args=(design, offset, labels, weights / weights.sum()), jac=True,
                            method="L-BFGS-B", options={"maxiter": 500, "ftol": 1e-12, "gtol": 1e-8})
        if not solution.success or not np.isfinite(solution.x).all():
            raise ValueError(f"Logistic fit failed: {solution.message}")
        artifact.update(mean=mean.tolist(), scale=scale.tolist(), beta=solution.x.tolist(), l2=.01,
                        iterations=int(solution.nit), objective=float(solution.fun))
    else:
        import lightgbm as lgb
        params = {**PARAMS, "device_type": "cpu", "deterministic": True, "force_col_wise": True}
        training = lgb.Dataset(x, label=labels, init_score=offset, weight=weights,
                               feature_name=preprocessing["matrix_columns"])
        booster = lgb.train(params, training, num_boost_round=150)
        booster.save_model(str(output / "residual.txt"))
        artifact.update(params=params, rounds=150)
    artifact["train_seconds"] = time.perf_counter() - start
    _write_json(output / "model.json", artifact)
    return artifact


def train_predict(dataset_path, feature_spec_path, *, group, model, fold, output,
                  plan_path, expected_plan_sha256, expected_feature_spec_sha256):
    """Run one predeclared group/fold; no model selection, tuning or promotion.

    Plan binds feature_spec_sha256 and data.dataset_sha256. Specification binds
    dataset_sha256, groups and point-in-time feature_evidence. Causal historical
    construction is not a claim of verified historical T-5 receipt.
    """
    if group not in GROUPS or model not in {"market", "logistic", "lightgbm"} or fold not in {"development", "validation"}:
        raise ValueError("Unsupported fixed comparison")
    if (group == "A") != (model == "market"):
        raise ValueError("A is raw market only; other groups require a residual learner")
    spec, provenance = _contract(dataset_path, feature_spec_path, plan_path,
                                 expected_plan_sha256, expected_feature_spec_sha256)
    columns, skip = _features(spec, group)
    directory = Path(output)
    directory.mkdir(parents=True, exist_ok=False)
    result = {"status": "skipped" if skip else "completed", "group": group, "model": model,
              "device": "cpu", "fold": fold, "provenance": provenance,
              "period_label": "development" if fold == "development" else "research-known validation",
              "market_timing": "retrospective_market_proxy; historical T-5 availability unverified",
              "automatic_promotion": False}
    if skip:
        result["skip_reason"] = skip
        _write_json(directory / "result.json", result)
        return result
    # Filter at the parquet reader, not after materializing later outcomes.
    data = pd.read_parquet(dataset_path, filters=[("race_date", ">=", "20210101"),
                                                ("race_date", "<=", "20221231")], use_threads=False)
    if len(data) != pq.read_metadata(dataset_path).num_rows:
        raise ValueError("Input contains rows outside the fixed 2021/2022 research scope")
    seed_rows = int(data.phase.eq("calibration_seed").sum()) if "phase" in data else 0
    data = _modeling_view(data)
    _validate_cohort(data)
    phases = ["meta_fit"] if fold == "development" else ["meta_fit", "development"]
    train = data.loc[data.phase.isin(phases)].reset_index(drop=True)
    test = data.loc[data.phase.eq("development" if fold == "development" else "validation")].reset_index(drop=True)
    if train.empty or test.empty or str(train.race_date.max()) >= str(test.race_date.min()):
        raise ValueError("Chronological training/evaluation overlap or empty fold")
    result.update(calibration_seed_rows_excluded_from_model_fit_and_evaluation=seed_rows,
                  train_rows=len(train), train_races=int(train.race_id.nunique()),
                  evaluation_rows=len(test), evaluation_races=int(test.race_id.nunique()),
                  train_date_from=str(train.race_date.min()), train_date_to=str(train.race_date.max()),
                  evaluation_date_from=str(test.race_date.min()), evaluation_date_to=str(test.race_date.max()),
                  train_cohort_sha256=_cohort_hash(train), evaluation_cohort_sha256=_cohort_hash(test))
    if group == "A":
        probabilities = test.normalized_market_probability.to_numpy(float).copy()
        result.update(feature_count=0, train_seconds=0., inference_seconds=0., rounds=None)
    else:
        preprocessing, skip = _fit_preprocess(train, columns, MARKET_FEATURES if group == "F" else FEATURES)
        if skip:
            result.update(status="skipped", skip_reason=skip)
            _write_json(directory / "result.json", result)
            return result
        artifacts = directory / "artifacts"
        artifacts.mkdir()
        with threadpool_limits(limits=2):
            artifact = _fit_model(train, preprocessing, model, artifacts)
            start = time.perf_counter()
            inference = test[["race_id", *columns]].copy()
            probabilities = predict_model(inference, artifacts)
        result.update(feature_count=len(artifact["features"]), input_feature_count=len(columns),
                      features=artifact["features"], train_seconds=artifact["train_seconds"],
                      inference_seconds=time.perf_counter() - start,
                      rounds=150 if model == "lightgbm" else None)
    # Recheck inputs before publication: never silently publish a changed contract.
    _contract(dataset_path, feature_spec_path, plan_path, expected_plan_sha256, expected_feature_spec_sha256)
    predictions = test[["race_id", "horse_number", "race_date"]].copy()
    predictions["probability"] = probabilities
    predictions.to_parquet(directory / "predictions.parquet", index=False)
    result["max_probability_sum_error"] = float(abs(pd.Series(probabilities).groupby(test.race_id).sum() - 1).max())
    result["artifact_sha256"] = {str(p.relative_to(directory)).replace("\\", "/"): sha256(p)
                                for p in sorted(directory.rglob("*")) if p.is_file()}
    _write_json(directory / "result.json", result)
    return result
