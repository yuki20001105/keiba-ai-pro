"""Isolated feature-search dataset built only from known pre-race predictors.

The source is the frozen v2 safe dataset, never a live database. Race-relative
values compare the current field's already-known historical features. They do
not inspect this race's result, time, odds, popularity, or payout. These are
candidate transforms, not features selected using any evaluation outcome.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

import numpy as np
import pandas as pd

from .dataset import EVALUATION_COLUMNS
from .features import ALLOWED_FEATURES, BASE_CATEGORICAL, FEATURE_GROUPS, FEATURE_SCHEMA_VERSION


DATASET_SCHEMA = "experiment-safe-search-dataset-v1"
SEARCH_FEATURE_SCHEMA = "experiment-safe-search-features-v1"
SOURCE_DATASET_SCHEMA = "experiment-jra-flat-dataset-v2"

# name, source, direction: positive relative values always mean historically
# stronger. For historical finish, a smaller source value is better.
RELATIVE_SOURCES = (
    ("prev_speed", "safe_prev_speed_mps", 1),
    ("past3_speed", "safe_past3_avg_speed_mps", 1),
    ("past5_speed", "safe_past5_avg_speed_mps", 1),
    ("past10_speed", "safe_past10_avg_speed_mps", 1),
    ("past3_finish", "safe_past3_avg_finish", -1),
    ("past5_finish", "safe_past5_avg_finish", -1),
    ("past5_win_rate", "safe_past5_win_rate", 1),
    ("jockey_win_rate", "safe_jockey_win_rate", 1),
)
TRENDS = {
    "safe_search_speed_trend_3_10": ("safe_past3_avg_speed_mps", "safe_past10_avg_speed_mps"),
    "safe_search_speed_trend_5_10": ("safe_past5_avg_speed_mps", "safe_past10_avg_speed_mps"),
    "safe_search_finish_improvement_3_10": ("safe_past10_avg_finish", "safe_past3_avg_finish"),
    "safe_search_win_rate_trend_3_10": ("safe_past3_win_rate", "safe_past10_win_rate"),
}
MISSING_FLAGS = {
    "safe_search_missing_prev": "safe_prev_speed_mps",
    "safe_search_missing_prev2": "safe_prev2_speed_mps",
    "safe_search_missing_jockey_history": "safe_jockey_win_rate",
}
SUPPORT_SOURCES = {
    "safe_search_log_prior_starts": "safe_prior_starts",
    "safe_search_log_jockey_starts": "safe_jockey_starts",
    "safe_search_log_trainer_starts": "safe_trainer_starts",
    "safe_search_log_course_starts": "safe_horse_course_starts",
}
SEARCH_FEATURE_GROUPS = {
    "search_relative": [f"safe_search_rel_{name}_{measure}"
                        for name, _, _ in RELATIVE_SOURCES for measure in ("rank", "delta")],
    "search_trend": list(TRENDS),
    "search_missing": list(MISSING_FLAGS),
    "search_adaptation": ["safe_search_log_days_since_last", "safe_search_distance_change_ratio"],
    "search_support": list(SUPPORT_SOURCES),
}
SEARCH_FEATURES = tuple(name for group in SEARCH_FEATURE_GROUPS.values() for name in group)
SOURCE_FEATURES = frozenset(
    [name for _, name, _ in RELATIVE_SOURCES]
    + [name for pair in TRENDS.values() for name in pair]
    + list(MISSING_FLAGS.values()) + list(SUPPORT_SOURCES.values())
    + ["safe_days_since_last", "safe_distance", "safe_prev_distance"]
)


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").astype(float).replace([np.inf, -np.inf], np.nan)


def derive_search_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Return 29 row-aligned candidates; read only race_id and safe sources.

    Unknown numeric values remain NaN, except explicit missingness indicators.
    A relative statistic requires at least two observed runners. Tied ranks
    use their average, normalized to [0, 1]; all-equal observed fields get 0.5.
    Race-relative comparisons assume the known current runner field, not the
    final outcome. No global fit, target fitting, or label-based selection runs.
    """
    if not SOURCE_FEATURES.issubset(ALLOWED_FEATURES):
        raise ValueError("search-source-allowlist-invalid")
    if frame.empty or not {"race_id", *SOURCE_FEATURES}.issubset(frame.columns):
        raise ValueError("search-feature-input-incomplete")
    # This physically restricts the computation view to approved inputs.
    inputs = frame.loc[:, ["race_id", *sorted(SOURCE_FEATURES)]]
    race = inputs["race_id"].astype("string")
    if race.isna().any() or not race.str.fullmatch(r"\d{12}").all():
        raise ValueError("search-race-identity-invalid")
    values = {name: _numeric(inputs[name]) for name in SOURCE_FEATURES}
    output = {}
    for name, original, direction in RELATIVE_SOURCES:
        oriented = values[original] * direction
        grouped = oriented.groupby(race, sort=False, observed=True)
        count = grouped.transform("count")
        enough = count.ge(2)
        rank = (grouped.rank(method="average", na_option="keep") - 1) / (count - 1).where(enough)
        output[f"safe_search_rel_{name}_rank"] = rank.where(enough)
        output[f"safe_search_rel_{name}_delta"] = (oriented - grouped.transform("mean")).where(enough)
    for name, (left, right) in TRENDS.items():
        output[name] = values[left] - values[right]
    for name, original in MISSING_FLAGS.items():
        output[name] = values[original].isna().astype(float)
    days = values["safe_days_since_last"]
    output["safe_search_log_days_since_last"] = np.log1p(days.where(days.gt(0)))
    distance, previous = values["safe_distance"], values["safe_prev_distance"]
    output["safe_search_distance_change_ratio"] = (
        (distance.where(distance.gt(0)) - previous) / previous.where(previous.gt(0))
    )
    for name, original in SUPPORT_SOURCES.items():
        counts = values[original]
        output[name] = np.log1p(counts.where(counts.ge(0)))
    result = pd.DataFrame(output, index=frame.index).loc[:, list(SEARCH_FEATURES)]
    result = result.replace([np.inf, -np.inf], np.nan).astype("float32")
    if len(result) != len(frame) or not result.index.equals(frame.index):
        raise ValueError("search-feature-row-alignment-invalid")
    return result


def _validate_source_manifest(manifest: dict) -> list[str]:
    if (manifest.get("dataset_schema_version") != SOURCE_DATASET_SCHEMA
            or manifest.get("feature_schema_version") != FEATURE_SCHEMA_VERSION):
        raise ValueError("search-requires-safe-v2-dataset")
    for key in ("dataset_sha256", "snapshot_sha256", "eligibility_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(manifest.get(key, ""))):
            raise ValueError("search-parent-provenance-invalid")
    for key in ("source_contract", "point_in_time_audit", "scope", "training_eligibility"):
        if not isinstance(manifest.get(key), dict):
            raise ValueError("search-parent-provenance-missing")
    audit = manifest["point_in_time_audit"]
    if (audit.get("strict_predicate") != "source_race_date < target_race_date"
            or audit.get("same_day_future_references") != 0
            or audit.get("future_leakage_rows") != 0
            or audit.get("same_day_results_excluded") is not True):
        raise ValueError("search-parent-history-contract-invalid")
    expected = [name for group in FEATURE_GROUPS.values() for name in group]
    sets = manifest.get("feature_sets", {})
    if (sets.get("M03") != expected or sets.get("M05") != expected
            or sets.get("M01") != FEATURE_GROUPS["base"]
            or sets.get("M02") != FEATURE_GROUPS["base"] + FEATURE_GROUPS["history"]
            or manifest.get("feature_groups") != FEATURE_GROUPS
            or manifest.get("categorical_features") != list(BASE_CATEGORICAL)):
        raise ValueError("search-parent-feature-allowlist-invalid")
    return expected


def _validate_source_frame(frame: pd.DataFrame, manifest: dict) -> None:
    if frame.empty or set(frame.columns) != set(EVALUATION_COLUMNS) | ALLOWED_FEATURES:
        raise ValueError("search-parent-columns-invalid")
    if manifest.get("row_count") != len(frame) or manifest.get("race_count") != frame["race_id"].nunique():
        raise ValueError("search-parent-cohort-count-invalid")
    race = frame["race_id"].astype("string")
    day = frame["race_date"].astype("string")
    number = _numeric(frame["horse_number"])
    declared = _numeric(frame["num_horses"])
    observed = race.groupby(race, sort=False).transform("size")
    if (race.isna().any() or not race.str.fullmatch(r"\d{12}").all()
            or day.isna().any() or not day.str.fullmatch(r"\d{8}").all()
            or pd.to_datetime(day, format="%Y%m%d", errors="coerce").isna().any()
            or day.groupby(race).nunique(dropna=False).ne(1).any()
            or not number.between(1, 18).all() or not number.eq(number.round()).all()
            or frame.duplicated(["race_id", "horse_number"]).any()
            or not declared.eq(observed).all()):
        raise ValueError("search-parent-runner-cohort-invalid")


def build_search_dataset(source_dataset_dir: Path, output_dir: Path) -> dict:
    """Publish a new, immutable candidate dataset without modifying its parent.

    Current evaluation columns pass through untouched, but are never inputs to
    candidate construction. Parent snapshot/eligibility/history contracts are
    retained; a separate search_feature_contract binds the exact parent bytes.
    """
    source = Path(source_dataset_dir)
    output = Path(output_dir)
    if source.is_symlink() or not source.is_dir() or output.is_symlink():
        raise ValueError("search-directory-invalid")
    source, output = source.resolve(strict=True), output.resolve()
    if output == source or output.is_relative_to(source):
        raise ValueError("search-output-must-be-separate")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise FileExistsError("search-output-must-be-empty")
    source_manifest = source / "dataset_manifest.json"
    source_parquet = source / "dataset.parquet"
    if any(path.is_symlink() or not path.is_file() for path in (source_manifest, source_parquet)):
        raise ValueError("search-parent-files-invalid")
    manifest_bytes = source_manifest.read_bytes()
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    parent = json.loads(manifest_bytes)
    if not isinstance(parent, dict):
        raise ValueError("search-parent-manifest-invalid")
    original_features = _validate_source_manifest(parent)
    parent_digest = _digest(source_parquet)
    if parent_digest != parent["dataset_sha256"]:
        raise ValueError("search-parent-dataset-hash-mismatch")
    frame = pd.read_parquet(source_parquet)
    _validate_source_frame(frame, parent)
    generated = derive_search_features(frame)
    result = pd.concat([frame, generated], axis=1)
    if not result.loc[:, frame.columns].equals(frame):
        raise ValueError("search-parent-data-modified")
    if _digest(source_parquet) != parent_digest or _digest(source_manifest) != manifest_digest:
        raise ValueError("search-parent-mutated-during-build")
    all_features = original_features + list(SEARCH_FEATURES)
    manifest = copy.deepcopy(parent)
    manifest.update({
        "dataset_schema_version": DATASET_SCHEMA, "feature_schema_version": SEARCH_FEATURE_SCHEMA,
        "source_dataset_schema_version": parent["dataset_schema_version"],
        "source_feature_schema_version": parent["feature_schema_version"],
        "parent_feature_sets": copy.deepcopy(parent["feature_sets"]),
        "dataset_file": "dataset.parquet", "dataset_path": str(output / "dataset.parquet"),
        "feature_allowlist": all_features,
        "search_feature_contract": {
            "schema_version": 1, "source_dataset_dir": str(source),
            "source_dataset_sha256": parent_digest, "source_manifest_sha256": manifest_digest,
            "source_snapshot_sha256": parent["snapshot_sha256"],
            "source_eligibility_sha256": parent["eligibility_sha256"],
            "search_features_module_sha256": _digest(Path(__file__)),
            "row_count": len(frame), "race_count": int(frame["race_id"].nunique()),
            "cohort_and_original_values_preserved": True,
            "allowed_source_features": sorted(SOURCE_FEATURES), "grouping_columns": ["race_id"],
            "added_features": list(SEARCH_FEATURES), "added_feature_count": len(SEARCH_FEATURES),
            "uses_current_outcomes_or_market": False, "label_based_selection": False,
            "relative_semantics": "Known predictors of the same current runner field only; higher rank/delta means stronger history.",
            "rank_formula": "(average ascending rank of oriented value - 1) / (observed count - 1)",
            "delta_formula": "oriented value - same-race mean of observed oriented values",
            "minimum_relative_observations": 2,
            "relative_inputs": [{"name": name, "source": field, "direction": direction}
                                for name, field, direction in RELATIVE_SOURCES],
            "trend_formulas": {name: f"{left} - {right}" for name, (left, right) in TRENDS.items()},
            "missing_flag_sources": MISSING_FLAGS.copy(),
            "rest_formula": "log1p(safe_days_since_last), strictly positive observed gap only",
            "distance_formula": "(safe_distance - safe_prev_distance) / safe_prev_distance, both positive",
            "support_formula": "log1p(observed nonnegative count); NaN remains unknown",
            "support_sources": SUPPORT_SOURCES.copy(),
            "unknown_policy": "NaN preserved except explicit missingness flags; no fabricated zero win rates",
            "limitation": "Observed eligible history, not complete careers; current runner field and pre-race inputs must be known. Not a pre-day prediction contract.",
        },
    })
    manifest["feature_sets"]["M03"] = all_features.copy()
    manifest["feature_sets"]["M05"] = all_features.copy()
    manifest["feature_groups"].update({name: list(columns) for name, columns in SEARCH_FEATURE_GROUPS.items()})
    manifest["missingness"] = {name: {"missing": int(result[name].isna().sum()),
                                        "fraction": float(result[name].isna().mean())}
                               for name in result.columns}
    output.mkdir(parents=True, exist_ok=True)
    temp_parquet = output / f".search-{uuid.uuid4().hex}.parquet.tmp"
    temp_manifest = output / f".search-manifest-{uuid.uuid4().hex}.json.tmp"
    final_parquet, final_manifest = output / "dataset.parquet", output / "dataset_manifest.json"
    try:
        result.to_parquet(temp_parquet, index=False, engine="pyarrow", compression="zstd")
        manifest["dataset_sha256"] = _digest(temp_parquet)
        temp_manifest.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                                            indent=2, allow_nan=False) + "\n", encoding="utf-8")
        # Exclusive no-overwrite publication; the manifest is the commit marker.
        os.link(temp_parquet, final_parquet)
        try:
            os.link(temp_manifest, final_manifest)
        except BaseException:
            final_parquet.unlink()
            raise
        return manifest
    finally:
        temp_parquet.unlink(missing_ok=True)
        temp_manifest.unlink(missing_ok=True)
