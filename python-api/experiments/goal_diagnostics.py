"""Read-only, development-window diagnostics and bounded research proposals.

Missingness describes the selected dataset cohort, not provider availability.
Proposals neither authorize feature creation nor perform data acquisition.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .common import file_sha256, json_sha256, read_json
from .training import _features


_REASONS = (
    "structural_missing", "not_available_at_prediction", "fetch_failed",
    "parse_failed", "storage_failed", "transform_failed", "unknown",
)
_UNKNOWN_IDS = {"", "none", "nan", "<na>", "unknown", "unknown_local", "null", "n/a", "-", "--", "不明"}


def _aggregate_eligibility(manifest: dict) -> dict:
    """Copy only documented aggregate fields, never arbitrary nested metadata."""
    source = manifest.get("training_eligibility", {})
    if not isinstance(source, dict):
        return {}
    result = {}
    for key in ("schema_version", "policy_version", "target",
                "exclusion_reason_counts_are_nonexclusive"):
        if key in source and isinstance(source[key], (str, int, bool)):
            result[key] = source[key]
    for key, fields in (
        ("requested_period", ("from", "to")),
        ("observed_eligible_date_range", ("from", "to")),
        ("quality_ledger", ("covered_races", "uncovered_races")),
    ):
        value = source.get(key)
        if isinstance(value, dict):
            result[key] = {field: value[field] for field in fields
                           if field in value and isinstance(value[field], (str, int, type(None)))}
    for key in ("counts", "exclusion_reason_counts"):
        value = source.get(key)
        if isinstance(value, dict):
            result[key] = {
                str(reason): {field: counts[field] for field in ("races", "rows")
                              if isinstance(counts.get(field), int)}
                for reason, counts in value.items()
                if isinstance(counts, dict) and
                (key != "counts" or reason in {"input", "eligible", "excluded"})
            }
    return result


def _history_requirement(column: str) -> tuple[str, int, bool] | None:
    if column in {"safe_days_since_last", "safe_distance_change"}:
        return "safe_prior_starts", 1, True
    previous = re.fullmatch(r"safe_(prev2?)_(finish|time_seconds|distance|speed_mps)", column)
    if previous:
        return "safe_prior_starts", 2 if previous[1] == "prev2" else 1, True
    window = re.fullmatch(
        r"safe_(past\d+)_(avg_(finish|speed_mps|last3f_time)|win_rate|top3_rate)", column,
    )
    if window:
        return f"safe_{window[1]}_count", 1, True
    group = re.fullmatch(
        r"safe_(horse_(surface|distance_band|course)|jockey|trainer|sire|damsire)"
        r"_(win_rate|top3_rate|avg_speed_mps)", column,
    )
    if group:
        return f"safe_{group[1]}_starts", 1, group[1].startswith("horse_")
    return None


def diagnose_dataset(
    dataset_dir: Path, development_years: list[int], train_start_year: int = 2016,
) -> dict:
    """Diagnose allowed M03 features from train start through development end.

    Parquet projection excludes outcomes and filters exclude confirmation rows.
    The full-file hash necessarily covers bytes outside the diagnostic window.
    """
    if (not isinstance(development_years, list) or not development_years
            or any(isinstance(year, bool) or not isinstance(year, int)
                   or not 1 <= year <= 9999 for year in development_years)):
        raise ValueError("development_years must be a nonempty list of calendar years")
    years = sorted(set(development_years))
    if (isinstance(train_start_year, bool) or not isinstance(train_start_year, int)
            or not 1 <= train_start_year <= min(years)):
        raise ValueError("train_start_year must not follow development_years")
    dataset_dir = Path(dataset_dir)
    manifest = read_json(dataset_dir / "dataset_manifest.json")
    path = dataset_dir / "dataset.parquet"
    digest = file_sha256(path)
    if digest != manifest.get("dataset_sha256"):
        raise ValueError("dataset hash mismatch")
    columns, categorical = _features(manifest, {"candidate_id": "M03"})
    schema = pq.read_schema(path)
    required = set(columns) | {"race_date", "race_id"}
    if not required.issubset(schema.names):
        raise ValueError(f"dataset missing diagnostic columns: {sorted(required - set(schema.names))}")
    projected = list(dict.fromkeys(
        ["race_date", "race_id"] + columns
        + [name for name in ("horse_id", "venue", "safe_prior_starts") if name in schema.names]
    ))
    lower, upper = f"{train_start_year:04d}0101", f"{max(years):04d}1231"
    date_type = schema.field("race_date").type
    if pa.types.is_integer(date_type):
        lower, upper = int(lower), int(upper)
    elif not (pa.types.is_string(date_type) or pa.types.is_large_string(date_type)):
        raise ValueError("race_date must contain YYYYMMDD strings or integers")
    frame = pd.read_parquet(
        path, engine="pyarrow", columns=projected,
        filters=[("race_date", ">=", lower), ("race_date", "<=", upper)],
    )
    if frame.empty:
        raise ValueError("empty development diagnostic frame")
    dates = pd.to_datetime(frame["race_date"].astype("string"), format="%Y%m%d", errors="coerce")
    if dates.isna().any():
        raise ValueError("diagnostic race_date must be valid YYYYMMDD")
    months = dates.dt.strftime("%Y-%m")
    venue = next((frame[name].astype("string").str.strip().fillna("<unknown>").replace("", "<unknown>")
                  for name in ("venue", "safe_venue") if name in frame), None)
    known_ids = {}
    for name in ("horse_id", "race_id"):
        known_ids[name] = (
            ~frame[name].astype("string").str.strip().str.lower().fillna("").isin(_UNKNOWN_IDS)
            if name in frame else pd.Series(False, index=frame.index)
        )
    history_masks = {}
    features, numeric = {}, {}
    for name in columns:
        if name in categorical:
            values = frame[name].astype("string").str.strip().replace("", pd.NA)
        else:
            values = pd.to_numeric(frame[name], errors="coerce").replace([np.inf, -np.inf], np.nan)
            if name.startswith("safe_"):
                numeric[name] = values
        missing = values.isna()
        requirement = _history_requirement(name)
        structural = 0
        if requirement is not None and requirement[0] in frame:
            if requirement not in history_masks:
                counter, minimum, horse_required = requirement
                counts = pd.to_numeric(frame[counter], errors="coerce").replace(
                    [np.inf, -np.inf], np.nan,
                )
                mask = known_ids["race_id"] & counts.ge(0) & counts.lt(minimum) & counts.mod(1).eq(0)
                history_masks[requirement] = mask & known_ids["horse_id"] if horse_required else mask
            structural = int((missing & history_masks[requirement]).sum())
        reasons = dict.fromkeys(_REASONS, 0)
        reasons["structural_missing"] = structural
        reasons["unknown"] = int(missing.sum()) - structural
        features[name] = {
            "missing_count": int(missing.sum()),
            "missing_fraction": float(missing.mean()),
            "zero_fraction": None if name in categorical else float(values.eq(0).mean()),
            "constant": bool(values.nunique(dropna=True) <= 1),
            "missing_reason_counts": reasons,
            "by_month": {str(key): float(value) for key, value in missing.groupby(months).mean().items()},
            "by_venue": {} if venue is None else {
                str(key): float(value) for key, value in missing.groupby(venue).mean().items()
            },
        }
    correlations = []
    correlation = pd.DataFrame(numeric).corr(min_periods=2)
    names = sorted(numeric)
    for index, left in enumerate(names):
        for right in names[index + 1:]:
            value = correlation.loc[left, right]
            if np.isfinite(value) and abs(value) >= 0.9:
                correlations.append({"left": left, "right": right, "correlation": float(value)})
    return {
        "schema": "goal-loop-diagnostics-v1", "dataset_sha256": digest,
        "development_years": years, "train_start_year": train_start_year,
        "row_count": len(frame), "race_count": int(frame["race_id"].nunique()),
        "feature_columns": columns, "features": features,
        "high_correlation": correlations,
        "training_eligibility": _aggregate_eligibility(manifest),
        "no_future_rows_read": True, "acquisition_requests_performed": 0,
        "provenance": {
            "caveat": "Missingness describes the selected cohort, not all races or source availability.",
            "missing_reason_policy": (
                "Only feature-specific observed-history counters establish structural missingness; "
                "unknown identities or counters remain unknown. Zero observed history in this "
                "eligible snapshot does not prove a career debut or complete source history."
            ),
            "research_flags_authorize_feature_creation_or_communication": False,
            "training_eligibility_scope": "Aggregate source manifest information; not recomputed for this window.",
        },
    }


def propose_hypotheses(diagnostics: dict, manifest: dict, max_proposals: int = 3) -> list[dict]:
    """Return deterministic proposals, not executable acquisition instructions.

    Priority is regularization, unknown-missingness review, whole-group
    ablation, then target comparison. Training proposals keep existing features.
    """
    if isinstance(max_proposals, bool) or not isinstance(max_proposals, int) or not 1 <= max_proposals <= 3:
        raise ValueError("max_proposals must be an integer from 1 to 3")
    columns, _ = _features(manifest, {"candidate_id": "M03"})
    if diagnostics.get("schema") != "goal-loop-diagnostics-v1":
        raise ValueError("unsupported diagnostics schema")
    if diagnostics.get("dataset_sha256") != manifest.get("dataset_sha256"):
        raise ValueError("diagnostics dataset hash mismatch")
    if diagnostics.get("feature_columns") != columns:
        raise ValueError("diagnostics features must match the allowed M03 feature set")
    proposals = []

    def add(kind, selected, reason, evidence, *, params=None, target="speed_deviation", approval=False):
        proposal = {
            "kind": kind, "target": target, "feature_columns": list(selected),
            "params": {} if params is None else params, "reason": reason,
            "evidence": evidence, "requires_approval": approval,
        }
        proposal["id"] = f"{kind}-{json_sha256(proposal)[:12]}"
        proposals.append(proposal)

    add("regularization", columns, "Compare a fixed, lower-capacity M03 model.",
        {"feature_set": "M03", "row_count": diagnostics["row_count"]},
        params={"num_leaves": 15, "min_data_in_leaf": 80, "lambda_l2": 1})
    stats = diagnostics.get("features", {})
    unknown = [
        name for name in columns
        if stats.get(name, {}).get("missing_fraction", 0) >= 0.5
        and stats.get(name, {}).get("missing_reason_counts", {}).get("unknown", 0) > 0
    ]
    if unknown:
        add("acquisition_review", unknown,
            "Review provenance for high unknown missingness; no cause or permission to fetch is inferred.",
            {name: copy.deepcopy(stats[name]["missing_reason_counts"]) for name in unknown},
            approval=True)
    base = set(manifest.get("feature_sets", {}).get("M01", []))
    groups = manifest.get("feature_groups", {})
    for group in sorted(groups):
        removed = groups[group]
        if (group == "base" or not isinstance(removed, list) or not removed
                or not set(removed).issubset(columns) or base.intersection(removed)):
            continue
        retained = [name for name in columns if name not in removed]
        if not retained:
            continue
        add("feature_ablation", retained, f"Compare removal of the entire optional {group} group.",
            {"group": group, "removed_feature_columns": list(removed),
             "missing_fractions": {name: stats.get(name, {}).get("missing_fraction") for name in removed}})
        break
    add("target_comparison", columns, "Compare ranking with the same allowed M03 features.",
        {"feature_set": "M03", "outcomes_inspected": False}, target="rank")
    return proposals[:max_proposals]
