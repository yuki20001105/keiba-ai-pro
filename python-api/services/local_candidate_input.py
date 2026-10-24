"""Hash-bound, transform-only local M01/M02 candidates, never active models.

The experimental feature builder and fitted fold encoder are reused read-only.
This adapter does not train, fetch, activate, or write to an acquisition ledger.
Feature parity is distinct from pre-race observation provenance and profitability.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime
from io import BytesIO
import hashlib
import json
import math
from pathlib import Path
import sqlite3

import joblib
import numpy as np
import pandas as pd

from experiments import dataset as canonical
from experiments import features as feature_source
from experiments import training as training_source
from forward_adapters import feature_state
from training import eligibility


SCHEMA = "local-safe-candidate-input-v1"
REPO_ROOT = Path(__file__).resolve().parents[2]
_TRIAL_ROOT = REPO_ROOT / "reports/generated/model-experiments/20260919-jra-safe-v1/trials"
_SPECS = {
    "local_safe_m01_2026": {
        "model_id": "local_safe_m01_2026", "candidate_id": "M01", "trial_id": "M01-final",
        "label": "速度・基本｜安全入力16｜学習〜2025", "evaluation_year": 2026,
        "sha256": "777617de1d45ff08fb37158c267c0ba5a777603c4db05f8ef60e38fb81adb322",
        "source_path": str(_TRIAL_ROOT / "M01-final/artifacts/fold_2026.joblib"),
        "feature_count": 16, "train_period": ["20160101", "20251231"],
    },
    "local_safe_m02_2025": {
        "model_id": "local_safe_m02_2025", "candidate_id": "M02", "trial_id": "M02-dev",
        "label": "速度・履歴｜安全入力47｜学習〜2024", "evaluation_year": 2025,
        "sha256": "59de569b86b14d8d391e3a5ba71d5b3b980a5bfc89f5397d2d30b7dc7e056e88",
        "source_path": str(_TRIAL_ROOT / "M02-dev/artifacts/fold_2025.joblib"),
        "feature_count": 47, "train_period": ["20160101", "20241231"],
    },
}
_SOURCE_PINS = {
    "experiments.dataset": "e5ea9260eb8d79592869a7e5efca1c6e992b3bd1d51ac75239796b75f3ef88e6",
    "experiments.features": "6ae59cc7643e353d3229430acc30e99bedd6c03c12af47d90e4854a6fdd67423",
    "experiments.training": "d4b2cc042425951aa65706ba382d38084f06f97a28701be7776574a1d645a828",
    "training.eligibility": "52dbd6522443f509331201b9b990cda253b8c88d34c5d211be088bc5ff4ad4d6",
    "forward_adapters.feature_state": "6af16e65c9289b746a4105f864b51685c88ff5777391904d88e8e074746fbf0b",
}
_CARD_FIELDS = {
    "race_id", "race_date", "date", "horse_id", "horse_number", "distance", "num_horses",
    "venue", "surface", "track_type", "bracket_number", "age", "sex", "sex_age",
    "burden_weight", "jockey_weight", "horse_weight", "weight_kg", "horse_weight_change",
    "weight_change", "field_condition", "weather", "race_class",
}
_FEATURE_LABELS = {
    "safe_distance": "距離", "safe_num_horses": "出走頭数", "safe_horse_number": "馬番",
    "safe_bracket_number": "枠番", "safe_age": "年齢", "safe_burden_weight": "斤量",
    "safe_horse_weight": "馬体重", "safe_weight_change": "馬体重増減", "safe_month": "開催月",
    "safe_race_number": "レース番号", "safe_surface": "芝・ダート", "safe_venue": "競馬場",
    "safe_sex": "性別", "safe_field_condition": "馬場状態", "safe_weather": "天候",
    "safe_race_class": "レースクラス", "safe_prior_starts": "記録済み過去走数",
    "safe_days_since_last": "前走からの日数", "safe_prev_finish": "前走着順",
    "safe_prev_time_seconds": "前走タイム", "safe_prev_distance": "前走距離",
    "safe_prev_speed_mps": "前走速度", "safe_prev2_finish": "前々走着順",
    "safe_prev2_time_seconds": "前々走タイム", "safe_prev2_distance": "前々走距離",
    "safe_prev2_speed_mps": "前々走速度",
}
for _window in (3, 5, 10):
    for _suffix, _label in {
        "count": "記録数", "observed_coverage": "記録割合", "avg_finish": "平均着順",
        "avg_speed_mps": "平均速度", "avg_last3f_time": "平均上がり3F",
        "win_rate": "勝率", "top3_rate": "3着内率",
    }.items():
        _FEATURE_LABELS[f"safe_past{_window}_{_suffix}"] = f"過去{_window}走・{_label}"


def candidate_specs() -> list[dict]:
    """Return a fresh allowlist; registration must not replace the active pointer."""
    return deepcopy(list(_SPECS.values()))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _modules_verified() -> bool:
    modules = (canonical, feature_source, training_source, eligibility, feature_state)
    try:
        return all(_sha(Path(module.__file__)) == _SOURCE_PINS[module.__name__] for module in modules)
    except (OSError, TypeError):
        return False


def _validated_candidate_bytes(model_path: str | Path, model_id: str | None = None) -> tuple[bytes, dict]:
    path = Path(model_path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("local-candidate-artifact-file-required")
    try:
        body = path.read_bytes()
    except OSError as exc:
        raise ValueError("local-candidate-artifact-file-required") from exc
    digest = hashlib.sha256(body).hexdigest()
    specs = [spec for spec in _SPECS.values() if spec["sha256"] == digest]
    if len(specs) != 1 or (model_id is not None and specs[0]["model_id"] != model_id):
        raise ValueError("local-candidate-untrusted-artifact-sha256")
    if not _modules_verified():
        raise ValueError("local-candidate-frozen-feature-source-changed")
    return body, specs[0]


def validate_candidate_sources(model_path: str | Path) -> dict:
    """Cheap cache-admission fence: exact artifact/source SHA, no deserialization.

    File mtime and size alone are not integrity evidence. Call on every cache
    admission, including a hit, and continue serving only the validated model.
    This proof does not assert an immutable live HTML/cutoff capture exists.
    """
    _, spec = _validated_candidate_bytes(model_path)
    return deepcopy(spec)


def _validate_bundle(bundle: dict, spec: dict) -> None:
    contract = bundle.get("contract", {})
    columns = contract.get("feature_columns", [])
    requested = feature_source.FEATURE_GROUPS["base"] + (
        feature_source.HISTORY_COLUMNS if spec["candidate_id"] == "M02" else []
    )
    encoder, model = bundle.get("encoder"), bundle.get("model")
    source = contract.get("source_contract", {})
    data_source = source.get("dataset_source_contract", {})
    if (contract.get("schema") != training_source.SCHEMA
            or contract.get("trial", {}).get("id") != spec["trial_id"]
            or contract.get("trial", {}).get("candidate_id") != spec["candidate_id"]
            or contract.get("evaluation_year") != spec["evaluation_year"]
            or contract.get("train_period") != spec["train_period"]
            or contract.get("task") != "speed_regression"
            or contract.get("market_features") is not False
            or contract.get("validation_used_for_fit_or_early_stopping") is not False
            or columns != requested or bundle.get("feature_columns") != columns
            or len(columns) != spec["feature_count"] or len(set(columns)) != len(columns)
            or type(encoder) is not training_source.FoldEncoder
            or encoder.requested_columns != requested or encoder.feature_columns != columns
            or not hasattr(model, "feature_name") or model.feature_name() != columns
            or model.num_feature() != len(columns)
            or source.get("training_module_sha256") != _SOURCE_PINS["experiments.training"]
            or data_source.get("dataset_module_sha256") != _SOURCE_PINS["experiments.dataset"]
            or data_source.get("features_module_sha256") != _SOURCE_PINS["experiments.features"]):
        raise ValueError("local-candidate-model-encoder-source-contract-mismatch")
    baseline = bundle.get("baseline") or {}
    if (baseline.get("schema") != "speed-deviation-baseline-v1"
            or not math.isfinite(float(baseline.get("global_mean", math.nan)))
            or not float(baseline.get("global_std", 0)) > 0):
        raise ValueError("local-candidate-training-baseline-required")
    if not _modules_verified():
        raise ValueError("local-candidate-frozen-feature-source-changed")


def load_candidate_bundle(model_path: str | Path, *, model_id: str | None = None) -> dict:
    """Verify exact trusted bytes *before* deserialization, including new copies.

    A path or user-selected model name cannot authorize arbitrary joblib input.
    The caller owns the separate candidate-copy manifest and selection UI.
    """
    body, spec = _validated_candidate_bytes(model_path, model_id)
    bundle = joblib.load(BytesIO(body))
    if not isinstance(bundle, dict):
        raise ValueError("local-candidate-bundle-required")
    _validate_bundle(bundle, spec)
    # Only compatibility metadata is added; no fitted object/value is changed.
    return {**bundle, "model_id": spec["model_id"], "target": "speed_deviation", "_local_candidate": deepcopy(spec),
            "_local_candidate_sha256": spec["sha256"], "calibrator": None, "optimizer": None}


def _current_card(frame: pd.DataFrame, race_info: dict | None) -> tuple[pd.DataFrame, dict]:
    if not isinstance(frame, pd.DataFrame) or frame.empty or frame.columns.duplicated().any():
        raise ValueError("local-candidate-complete-current-card-required")
    ids = frame.get("race_id", pd.Series(dtype="string")).dropna().astype(str).unique()
    if len(ids) != 1:
        raise ValueError("local-candidate-single-race-required")
    rid = ids[0]
    info = dict(race_info or frame.iloc[0].to_dict())
    # Prefer the authoritative race metadata, not a provider's prev_* profile.
    meta, reason = canonical._race_metadata(rid, info)
    if reason:
        raise ValueError(f"local-candidate-current-metadata-{reason}")
    rows = []
    for raw in frame.to_dict("records"):
        # Outcome fields, odds, prior profile values and safe_* claims are not
        # even passed to the canonical runner reader for the target race.
        card = {name: value for name, value in raw.items() if name in _CARD_FIELDS}
        value, reason = canonical._runner(rid, card, meta)
        if reason:
            raise ValueError(f"local-candidate-current-runner-{reason}")
        rows.append({name: value[name] for name in _CARD_FIELDS if name in value})
    current = pd.DataFrame(rows)
    feature_state._cohort(current, historical=False)
    return current, info


def _select_history(frame: pd.DataFrame, quality: dict, target_date: str) -> tuple[pd.DataFrame, dict]:
    if frame.empty:
        return frame.reindex(columns=canonical.SOURCE_COLUMNS), {"eligible_rows": 0, "eligible_races": 0}
    # Explicit injected frames fail, rather than silently discarding an unsafe
    # same-day/future observation. DB selection applies this before loading.
    if frame.race_date.ge(target_date).any() or frame.race_date.lt("20130101").any():
        raise ValueError("local-candidate-history-must-be-strictly-before-target")
    selection = eligibility.select_training_eligible_rows(
        frame, target="speed_deviation", recorded_quality_states=quality,
    )
    raw_counts = frame.groupby("race_id").size()
    selected_counts = selection.frame.groupby("race_id").size()
    partial = selected_counts.index[selected_counts.ne(raw_counts.reindex(selected_counts.index))]
    clean = selection.frame.loc[~selection.frame.race_id.isin(partial)].reset_index(drop=True)
    feature_state._cohort(clean, historical=True)
    return clean, {"eligible_rows": len(clean), "eligible_races": int(clean.race_id.nunique()),
                   "partial_races_excluded": len(partial), "eligibility": selection.manifest.as_dict()}


def load_candidate_history(db_path: str | Path, current: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """RO indexed horse lookup plus every runner in each related past race.

    M01/M02 consume horse history only, not entity/course population estimates.
    Complete fields are retained so eligibility cannot approve partial races.
    SQLite uses one consistent read transaction; missing IDs remain no-history.
    """
    target = str(current.race_date.iloc[0])
    horses = sorted({str(value) for value in current.horse_id if value})
    if not horses:
        return pd.DataFrame(columns=canonical.SOURCE_COLUMNS), {"eligible_rows": 0, "eligible_races": 0}
    path = Path(db_path).resolve(strict=True)
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    quarantine, quality, rows, counts = {}, {}, [], Counter()
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for sidecar in ("licensed_history_entries", "official_history_entries"):
            if sidecar in tables and connection.execute(f"SELECT 1 FROM {sidecar} LIMIT 1").fetchone():
                raise ValueError("local-candidate-sidecar-contract-not-supported")
        placeholders = ",".join("?" for _ in horses)
        # No full DB/profile materialization; use the existing horse JSON index.
        matched_ids = [str(row[0]) for row in connection.execute(
            "SELECT DISTINCT race_id FROM race_results_ultimate "
            f"WHERE json_extract(data,'$.horse_id') IN ({placeholders})", horses,
        )]
        for rid in matched_ids:
            metadata_rows = list(connection.execute("SELECT data FROM races_ultimate WHERE race_id=?", (rid,)))
            if len(metadata_rows) != 1:
                quarantine[rid] = "race_metadata_duplicate_or_missing"
                continue
            try:
                meta, reason = canonical._race_metadata(rid, json.loads(metadata_rows[0][0]))
            except (ValueError, TypeError):
                meta, reason = None, "race_json_invalid"
            if reason:
                quarantine[rid] = reason
                continue
            if meta["race_date"] >= target:
                counts["same_day_or_future_races_excluded"] += 1
                continue
            race_rows = []
            for payload, in connection.execute("SELECT data FROM race_results_ultimate WHERE race_id=?", (rid,)):
                try:
                    value, reason = canonical._runner(rid, json.loads(payload), meta)
                except (ValueError, TypeError):
                    value, reason = None, "runner_json_invalid"
                if reason:
                    quarantine[rid] = reason
                    break
                race_rows.append(value)
            if rid in quarantine:
                continue
            rows.extend(race_rows)
            if "scrape_race_acquisition_state" in tables:
                state = connection.execute(
                    "SELECT lifecycle,quality_status,excluded_from_standard "
                    "FROM scrape_race_acquisition_state WHERE race_id=?", (rid,),
                ).fetchone()
                if state is not None:
                    quality[rid] = (str(state[0] or ""), str(state[1] or ""), bool(state[2]))
    finally:
        connection.close()
    frame = pd.DataFrame(rows, columns=canonical.SOURCE_COLUMNS)
    clean, audit = _select_history(frame, quality, target)
    return clean, {**audit, **counts, "quarantined_races": quarantine,
                   "source": "ultimate_tables_readonly_indexed_horse_related_complete_races"}


def _capture_provenance(frame: pd.DataFrame, info: dict, day: str) -> bool:
    """Conservative same-day evidence only, not historical final-price leakage.

    data_observed_at is the provider observation timestamp, never created_at.
    All runner captures and race capture must precede the scheduled start. This
    is not a T-5 promise, authentication proof, or final-odds executability.
    """
    start = str(info.get("post_time") or "")
    try:
        from zoneinfo import ZoneInfo
        cutoff = datetime.strptime(day + " " + start, "%Y%m%d %H:%M").replace(tzinfo=ZoneInfo("Asia/Tokyo"))
        captures = [info.get("data_observed_at"), *frame.get("data_observed_at", pd.Series([None] * len(frame))).tolist()]
        for value in captures:
            observed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if observed.tzinfo is None or observed >= cutoff or observed.astimezone(cutoff.tzinfo).date() != cutoff.date():
                return False
        return bool(captures)
    except (TypeError, ValueError, KeyError):
        return False


def build_candidate_input(
    bundle: dict, frame: pd.DataFrame, *, db_path: str | Path | None = None,
    race_info: dict | None = None, historical_frame: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Build exactly the original encoder input; preserve the caller row order.

    An injected offline canonical history is accepted for parity tests only;
    its date/cohort/eligibility is checked just like stored rows. There is no
    legacy feature fallback and no numeric zero fill. Counts are observations.
    """
    spec = bundle.get("_local_candidate") or {}
    if spec.get("model_id") not in _SPECS or bundle.get("_local_candidate_sha256") != _SPECS[spec["model_id"]]["sha256"]:
        raise ValueError("local-candidate-verified-bundle-required")
    _validate_bundle(bundle, _SPECS[spec["model_id"]])
    current, info = _current_card(frame, race_info)
    day = str(current.race_date.iloc[0])
    if day <= bundle["contract"]["train_period"][1]:
        raise ValueError("local-candidate-target-must-be-after-model-training")
    if historical_frame is not None:
        history, history_audit = _select_history(historical_frame.copy(), {}, day)
        history_audit["source"] = "offline_injected_canonical_history"
    elif spec["candidate_id"] == "M01":
        history = pd.DataFrame(columns=canonical.SOURCE_COLUMNS)
        history_audit = {"source": "not_required_by_base_model", "eligible_rows": 0, "eligible_races": 0}
    elif db_path is not None:
        history, history_audit = load_candidate_history(db_path, current)
    else:
        raise ValueError("local-candidate-history-source-required")
    state = feature_state.build_history_state(history, target_date=day)
    engineered = feature_state.build_current_features(state, current)
    # Forward state is canonical horse-number sorted; response rows need the
    # exact request order, otherwise a valid score could be assigned to a horse.
    engineered = engineered.set_index("horse_number").loc[current.horse_number].reset_index()
    columns = bundle["feature_columns"]
    encoded = bundle["encoder"].transform(engineered.loc[:, columns]).reset_index(drop=True)
    if list(encoded) != columns or len(encoded) != len(frame):
        raise ValueError("local-candidate-encoded-order-or-count-mismatch")
    capture_verified = _capture_provenance(frame, info, day)
    # The adapter verifies a stored timestamp's position, not the provenance of
    # immutable pre-cutoff source bytes. Never let formula parity/timestamp
    # syntax alone satisfy the live purchasing input validator, including /api/
    # predict callers that can supply their own timestamp strings.
    issues = ["card_capture_provenance_unverified"]
    if not capture_verified:
        issues.append("card_capture_time_unverified")
    features_digest = hashlib.sha256(json.dumps(columns, separators=(",", ":")).encode()).hexdigest()
    return encoded, {
        "schema": "prediction-input-contract-v1", "status": "unverified",
        "scope": SCHEMA, "semantic_parity_verified": True,
        "expected_count": len(columns), "actual_count": len(encoded.columns),
        "missing_columns": [], "restored_columns": [], "blocked_columns": [],
        "all_missing_columns": [name for name in encoded if encoded[name].isna().all()],
        "missing_value_rate": float(encoded.isna().to_numpy().mean()),
        "training_feature_hash": _SOURCE_PINS["experiments.features"],
        "runtime_feature_hash": _SOURCE_PINS["experiments.features"],
        "source_hash_matches": True, "history_available": True, "issue_codes": issues,
        "feature_columns_sha256": features_digest, "model_artifact_sha256": spec["sha256"],
        "candidate_id": spec["candidate_id"], "adapter_version": SCHEMA,
        "card_capture_time_verified": capture_verified, "current_outcomes_consumed": False,
        "live_capture_hash_bound": False,
        "capture_proof_scope": "stored observation timestamp only; not immutable pre-cutoff HTML or source authentication proof",
        "history_max_source_date": state["state"]["history_max_source_date"],
        "history_source_rows_sha256": state["state"]["source_rows_sha256"],
        "history_state_sha256": state["state_sha256"], "history_audit": history_audit,
        "same_day_or_future_history_used": False, "score_is_probability": False,
        "calibration_verified": False, "profitability_verified": False,
    }


def predict_candidate_scores(bundle: dict, encoded: pd.DataFrame, *, num_threads: int = 2) -> tuple[np.ndarray, np.ndarray]:
    """Return speed-deviation scores and *uncalibrated display weights*, not wins."""
    if list(encoded) != bundle["feature_columns"]:
        raise ValueError("local-candidate-prediction-column-order-mismatch")
    raw = np.asarray(bundle["model"].predict(encoded, num_threads=num_threads), dtype=float)
    if raw.shape != (len(encoded),) or not np.isfinite(raw).all():
        raise ValueError("local-candidate-prediction-invalid")
    weights = np.exp(raw - raw.max())
    return raw, weights / weights.sum()


def candidate_speed_mps(bundle: dict, raw: np.ndarray, current: pd.DataFrame) -> np.ndarray:
    """Invert only the frozen fold's training target baseline (never refit)."""
    return training_source._inverse_speed(raw, current, bundle["baseline"])


def explain_candidate_scores(bundle: dict, encoded: pd.DataFrame, top_n: int = 6, *, num_threads: int = 2) -> list[dict]:
    """TreeSHAP on the same encoded matrix and speed-score scale as prediction."""
    if list(encoded) != bundle["feature_columns"]:
        raise ValueError("local-candidate-explanation-column-order-mismatch")
    contributions = np.asarray(bundle["model"].predict(encoded, pred_contrib=True, num_threads=num_threads))
    if contributions.shape != (len(encoded), len(encoded.columns) + 1) or not np.isfinite(contributions).all():
        raise ValueError("local-candidate-explanation-invalid")
    result = []
    for position, row in enumerate(contributions):
        impacts = row[:-1]
        total = float(np.abs(impacts).sum()) or 1.0
        details = []
        for index in np.argsort(np.abs(impacts))[::-1][:max(1, int(top_n))]:
            raw_value = encoded.iloc[position, int(index)]
            name = str(encoded.columns[index])
            details.append({"feature": name, "label": _FEATURE_LABELS.get(name, name),
                            "description": "発走前の出馬表または対象日より前の確定走から生成。過去走数は保存済み対象走の数。",
                            "value": float(raw_value) if np.isfinite(raw_value) else None,
                            "contribution": float(impacts[index]),
                            "impact_pct": float(abs(impacts[index]) / total * 100),
                            "direction": "positive" if impacts[index] >= 0 else "negative"})
        result.append({"method": "tree_shap", "base_value": float(row[-1]), "features": details,
                       "target": "speed_deviation", "contribution_scale": "速度スコアへの寄与"})
    return result
