from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from keiba_ai.constants import FUTURE_FIELDS, ID_COLUMNS, UNNECESSARY_COLUMNS  # type: ignore

from .generator import compute_feature_series


def _default_spec_path() -> Path:
    return Path(__file__).with_name("feature_generator.yaml")


def _default_candidates_path() -> Path:
    return Path(__file__).with_name("feature_discovery_candidates.yaml")


def _is_numeric_series(s: pd.Series) -> bool:
    return bool(pd.api.types.is_numeric_dtype(s))


def _safe_corr(a: pd.Series, b: pd.Series) -> float:
    try:
        x = pd.to_numeric(a, errors="coerce")
        y = pd.to_numeric(b, errors="coerce")
        if x.notna().sum() < 30 or y.notna().sum() < 30:
            return 0.0
        v = float(x.corr(y, method="spearman"))
        if v != v:
            return 0.0
        return abs(v)
    except Exception:
        return 0.0


def _feature_name(base: str, suffix: str) -> str:
    return f"fd_{base}_{suffix}".replace("__", "_")


def _candidate_nodes(df: pd.DataFrame, max_candidates: int, extra_nodes: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    banned = set(FUTURE_FIELDS) | set(ID_COLUMNS) | set(UNNECESSARY_COLUMNS)
    numeric_cols = [
        c for c in df.columns
        if c not in banned and _is_numeric_series(df[c]) and str(c).strip() != ""
    ]

    # Prioritize informative numeric columns by non-null variance.
    ranked: list[tuple[float, str]] = []
    for c in numeric_cols:
        s = pd.to_numeric(df[c], errors="coerce")
        if s.notna().sum() < 100:
            continue
        var = float(s.var(skipna=True)) if s.notna().any() else 0.0
        if var <= 0.0:
            continue
        ranked.append((var, c))
    ranked.sort(key=lambda x: x[0], reverse=True)
    top_cols = [c for _, c in ranked[:40]]

    nodes: list[dict[str, Any]] = []

    for gid in ("horse_id", "jockey_id", "trainer_id"):
        if gid not in df.columns:
            continue
        for src in top_cols[:16]:
            for w in (3, 5):
                nodes.append(
                    {
                        "name": _feature_name(src, f"{gid}_rm{w}"),
                        "type": "rolling_mean",
                        "source": src,
                        "group_by": gid,
                        "order_by": "race_id",
                        "window": int(w),
                        "min_periods": 1,
                    }
                )

    if "race_id" in df.columns:
        for src in top_cols[:24]:
            nodes.append(
                {
                    "name": _feature_name(src, "race_rank"),
                    "type": "race_rank",
                    "source": src,
                    "race_key": "race_id",
                    "ascending": False,
                }
            )

    for i in range(min(10, len(top_cols))):
        for j in range(i + 1, min(10, len(top_cols))):
            a = top_cols[i]
            b = top_cols[j]
            nodes.append(
                {
                    "name": _feature_name(f"{a}_{b}", "prod"),
                    "type": "interaction_product",
                    "lhs": a,
                    "rhs": b,
                }
            )
            nodes.append(
                {
                    "name": _feature_name(f"{a}_{b}", "ratio"),
                    "type": "interaction_ratio",
                    "numerator": a,
                    "denominator": b,
                    "eps": 1e-6,
                }
            )

    if extra_nodes:
        for node in extra_nodes:
            if isinstance(node, dict):
                nodes.append(node)

    if len(nodes) > max_candidates:
        nodes = nodes[:max_candidates]
    return nodes


def _evaluate_node(df: pd.DataFrame, node: dict[str, Any], target_col: str) -> dict[str, Any] | None:
    name = str(node.get("name") or "").strip()
    if not name:
        return None
    try:
        s = compute_feature_series(df, node)
    except Exception:
        return None

    s_num = pd.to_numeric(s, errors="coerce")
    non_null = int(s_num.notna().sum())
    if non_null < 100:
        return None

    null_rate = float(s_num.isna().mean())
    unique = int(s_num.nunique(dropna=True))
    unique_ratio = float(unique) / float(max(non_null, 1))
    variance = float(s_num.var(skipna=True)) if non_null > 1 else 0.0

    corr = 0.0
    if target_col in df.columns:
        corr = _safe_corr(s_num, pd.to_numeric(df[target_col], errors="coerce"))

    quality = 100.0
    quality -= null_rate * 50.0
    if unique_ratio < 0.001:
        quality -= 35.0
    elif unique_ratio < 0.01:
        quality -= 15.0
    if variance <= 0.0:
        quality -= 40.0
    quality = max(0.0, min(100.0, quality))

    utility = corr * 100.0
    total_score = round((quality * 0.55) + (utility * 0.45), 4)

    return {
        "name": name,
        "type": str(node.get("type") or ""),
        "node": node,
        "quality_score": round(quality, 4),
        "utility_score": round(utility, 4),
        "total_score": total_score,
        "null_rate": round(null_rate, 6),
        "unique_count": unique,
        "unique_ratio": round(unique_ratio, 6),
        "variance": round(variance, 6),
        "target_spearman_abs": round(corr, 6),
    }


def _promote_nodes(base_spec_path: Path, selected_nodes: list[dict[str, Any]]) -> dict[str, Any]:
    if base_spec_path.exists():
        try:
            cur = yaml.safe_load(base_spec_path.read_text(encoding="utf-8")) or {}
        except Exception:
            cur = {}
    else:
        cur = {}

    features = cur.get("features") if isinstance(cur.get("features"), list) else []
    existing_names = {
        str(n.get("name"))
        for n in features
        if isinstance(n, dict) and str(n.get("name") or "").strip()
    }

    added = 0
    for node in selected_nodes:
        name = str(node.get("name") or "").strip()
        if not name or name in existing_names:
            continue
        features.append(node)
        existing_names.add(name)
        added += 1

    cur["features"] = features
    base_spec_path.write_text(yaml.safe_dump(cur, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return {"added": int(added), "feature_count": int(len(features))}


def run_feature_discovery(
    *,
    df: pd.DataFrame,
    target_col: str = "win",
    max_candidates: int = 120,
    top_k: int = 20,
    min_total_score: float = 20.0,
    promote: bool = False,
    extra_nodes: list[dict[str, Any]] | None = None,
    base_spec_path: Path | None = None,
    candidates_output_path: Path | None = None,
) -> dict[str, Any]:
    n_candidates = max(20, min(int(max_candidates), 2000))
    n_top = max(1, min(int(top_k), 200))

    nodes = _candidate_nodes(df, max_candidates=n_candidates, extra_nodes=extra_nodes)

    evaluated: list[dict[str, Any]] = []
    for node in nodes:
        r = _evaluate_node(df, node, target_col=target_col)
        if r is None:
            continue
        if float(r.get("total_score") or 0.0) < float(min_total_score):
            continue
        evaluated.append(r)

    evaluated.sort(
        key=lambda x: (
            float(x.get("total_score") or 0.0),
            float(x.get("utility_score") or 0.0),
        ),
        reverse=True,
    )

    selected = evaluated[:n_top]
    selected_nodes = [x["node"] for x in selected if isinstance(x.get("node"), dict)]

    out_path = candidates_output_path or _default_candidates_path()
    snapshot = {
        "generated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "target_col": target_col,
        "max_candidates": n_candidates,
        "top_k": n_top,
        "selected_count": len(selected_nodes),
        "features": selected_nodes,
        "scores": [
            {
                "name": x.get("name"),
                "type": x.get("type"),
                "total_score": x.get("total_score"),
                "utility_score": x.get("utility_score"),
                "quality_score": x.get("quality_score"),
                "target_spearman_abs": x.get("target_spearman_abs"),
            }
            for x in selected
        ],
    }
    out_path.write_text(yaml.safe_dump(snapshot, sort_keys=False, allow_unicode=True), encoding="utf-8")

    promote_info = {"added": 0, "feature_count": 0}
    if promote and selected_nodes:
        promote_info = _promote_nodes(base_spec_path or _default_spec_path(), selected_nodes)

    return {
        "target_col": target_col,
        "input_rows": int(len(df)),
        "generated_candidates": int(len(nodes)),
        "evaluated_candidates": int(len(evaluated)),
        "selected_count": int(len(selected_nodes)),
        "selected_features": [x.get("name") for x in selected],
        "scores": selected,
        "candidates_file": str(out_path),
        "promote": {
            "enabled": bool(promote),
            **promote_info,
        },
    }
