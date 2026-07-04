from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import yaml


def _load_spec(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"features": []}
    try:
        obj = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {"features": []}
    return obj if isinstance(obj, dict) else {"features": []}


def _rolling_mean(df: pd.DataFrame, spec: dict[str, Any]) -> pd.Series:
    src = str(spec.get("source") or "")
    gid = str(spec.get("group_by") or "")
    ord_col = str(spec.get("order_by") or "race_id")
    window = int(spec.get("window", 3))
    min_periods = int(spec.get("min_periods", 1))
    if src not in df.columns or gid not in df.columns or ord_col not in df.columns:
        return pd.Series([0.0] * len(df), index=df.index)
    work = df[[gid, ord_col, src]].copy()
    work[src] = pd.to_numeric(work[src], errors="coerce")
    work["_idx"] = work.index
    work = work.sort_values([gid, ord_col], kind="mergesort")
    out = (
        work.groupby(gid, sort=False, observed=False)[src]
        .transform(lambda s: s.shift(1).rolling(window=window, min_periods=min_periods).mean())
        .fillna(0.0)
    )
    work["_out"] = out
    return work.set_index("_idx").reindex(df.index)["_out"].fillna(0.0)


def _rolling_topk_rate(df: pd.DataFrame, spec: dict[str, Any]) -> pd.Series:
    src = str(spec.get("source") or "")
    gid = str(spec.get("group_by") or "")
    ord_col = str(spec.get("order_by") or "race_id")
    window = int(spec.get("window", 10))
    top_k = int(spec.get("top_k", 3))
    min_periods = int(spec.get("min_periods", 3))
    if src not in df.columns or gid not in df.columns or ord_col not in df.columns:
        return pd.Series([0.0] * len(df), index=df.index)
    work = df[[gid, ord_col, src]].copy()
    finish = pd.to_numeric(work[src], errors="coerce")
    work["_flag"] = ((finish > 0) & (finish <= top_k)).astype(float)
    work["_idx"] = work.index
    work = work.sort_values([gid, ord_col], kind="mergesort")
    out = (
        work.groupby(gid, sort=False, observed=False)["_flag"]
        .transform(lambda s: s.shift(1).rolling(window=window, min_periods=min_periods).mean())
        .fillna(0.0)
    )
    work["_out"] = out
    return work.set_index("_idx").reindex(df.index)["_out"].fillna(0.0)


def _race_rank(df: pd.DataFrame, spec: dict[str, Any]) -> pd.Series:
    src = str(spec.get("source") or "")
    race_key = str(spec.get("race_key") or "race_id")
    ascending = bool(spec.get("ascending", True))
    if src not in df.columns or race_key not in df.columns:
        return pd.Series([0.0] * len(df), index=df.index)
    vals = pd.to_numeric(df[src], errors="coerce")
    return vals.groupby(df[race_key], observed=False).rank(method="min", ascending=ascending).fillna(0.0)


def _interaction_product(df: pd.DataFrame, spec: dict[str, Any]) -> pd.Series:
    lhs = str(spec.get("lhs") or "")
    rhs = str(spec.get("rhs") or "")
    if lhs not in df.columns or rhs not in df.columns:
        return pd.Series([0.0] * len(df), index=df.index)
    a = pd.to_numeric(df[lhs], errors="coerce").fillna(0.0)
    b = pd.to_numeric(df[rhs], errors="coerce").fillna(0.0)
    return (a * b).fillna(0.0)


def _interaction_ratio(df: pd.DataFrame, spec: dict[str, Any]) -> pd.Series:
    numerator = str(spec.get("numerator") or "")
    denominator = str(spec.get("denominator") or "")
    eps = float(spec.get("eps", 1e-6))
    if numerator not in df.columns or denominator not in df.columns:
        return pd.Series([0.0] * len(df), index=df.index)
    a = pd.to_numeric(df[numerator], errors="coerce").fillna(0.0)
    b = pd.to_numeric(df[denominator], errors="coerce").fillna(0.0)
    return (a / (b.abs() + eps)).replace([float("inf"), float("-inf")], 0.0).fillna(0.0)


def _scenario_semantic_match(df: pd.DataFrame, spec: dict[str, Any]) -> pd.Series:
    scenario_col = str(spec.get("scenario_col") or "")
    horse_col = str(spec.get("horse_col") or "")
    mode = str(spec.get("mode") or "higher_is_better")
    max_gate = float(spec.get("max_gate") or 18.0)

    if scenario_col not in df.columns or horse_col not in df.columns:
        return pd.Series([0.0] * len(df), index=df.index)

    s = pd.to_numeric(df[scenario_col], errors="coerce").fillna(0.0)
    h_raw = pd.to_numeric(df[horse_col], errors="coerce").fillna(0.0)

    if mode == "lower_is_better":
        # Smaller horse-side value means stronger match (ex: popularity, last_3f_time).
        h_norm = h_raw.rank(method="average", pct=True, na_option="bottom").fillna(0.0)
        fit = (1.0 - h_norm).clip(lower=0.0, upper=1.0)
    elif mode == "inside_gate_match":
        denom = max(1.0, float(max_gate))
        gate_norm = (h_raw / denom).clip(lower=0.0, upper=1.0)
        fit = (1.0 - gate_norm).clip(lower=0.0, upper=1.0)
    elif mode == "outside_gate_match":
        denom = max(1.0, float(max_gate))
        gate_norm = (h_raw / denom).clip(lower=0.0, upper=1.0)
        fit = gate_norm.clip(lower=0.0, upper=1.0)
    else:
        # Default: larger horse-side value means stronger match.
        h_norm = h_raw.rank(method="average", pct=True, na_option="bottom").fillna(0.0)
        fit = h_norm.clip(lower=0.0, upper=1.0)

    return (s.clip(lower=0.0, upper=1.0) * fit).fillna(0.0)


def compute_feature_series(df: pd.DataFrame, node: dict[str, Any]) -> pd.Series:
    ftype = str(node.get("type") or "").strip()
    if ftype == "rolling_mean":
        return _rolling_mean(df, node)
    if ftype == "rolling_topk_rate":
        return _rolling_topk_rate(df, node)
    if ftype == "race_rank":
        return _race_rank(df, node)
    if ftype == "interaction_product":
        return _interaction_product(df, node)
    if ftype == "interaction_ratio":
        return _interaction_ratio(df, node)
    if ftype == "scenario_semantic_match":
        return _scenario_semantic_match(df, node)
    raise ValueError(f"unsupported feature type: {ftype}")


def apply_feature_generator(df: pd.DataFrame, spec_path: Path | None = None) -> tuple[pd.DataFrame, list[str]]:
    path = spec_path or Path(__file__).with_name("feature_generator.yaml")
    spec = _load_spec(path)
    features = spec.get("features") if isinstance(spec.get("features"), list) else []

    out = df.copy()
    created: list[str] = []
    for node in features:
        if not isinstance(node, dict):
            continue
        name = str(node.get("name") or "").strip()
        ftype = str(node.get("type") or "").strip()
        if not name or not ftype:
            continue
        try:
            out[name] = compute_feature_series(out, node)
            created.append(name)
        except Exception:
            continue
    return out, created
