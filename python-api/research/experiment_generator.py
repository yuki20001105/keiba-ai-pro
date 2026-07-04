from __future__ import annotations

from typing import Any


def _build_feature_columns(tags: list[str]) -> list[str]:
    t = set(tags)
    cols: list[str] = []
    if "popularity" in t:
        cols.extend(["odds", "popularity"])
    if "condition" in t or "surface" in t:
        cols.extend(["field_condition", "surface", "distance"])
    if "distance" in t:
        cols.extend(["distance", "burden_weight", "horse_weight"])
    if not cols:
        cols.extend(["odds", "horse_weight"])
    # unique order-preserving
    out: list[str] = []
    for c in cols:
        if c not in out:
            out.append(c)
    return out


def generate_experiment_specs(
    *,
    plan: dict[str, Any],
    baseline_model_id: str,
    challenger_model_ids: list[str],
    max_specs: int = 20,
) -> list[dict[str, Any]]:
    items = plan.get("plan_items") if isinstance(plan.get("plan_items"), list) else []
    n = max(1, min(int(max_specs), 200))
    specs: list[dict[str, Any]] = []

    for i, item in enumerate(items):
        if len(specs) >= n:
            break
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or f"candidate_{i+1}")
        tags = [str(x) for x in (item.get("tags") or [])]
        priority = int(item.get("priority") or 50)

        spec = {
            "experiment": {
                "name": f"auto_{name}_{i+1:03d}",
                "tags": tags,
            },
            "models": {
                "baseline_model_id": baseline_model_id,
                "challenger_model_ids": [m for m in challenger_model_ids if m],
            },
            "backtest": {
                "enabled": True,
                "model_ids": [m for m in [baseline_model_id, *challenger_model_ids] if m],
                "stake_per_race": 100,
            },
            "feature_impact": {
                "enabled": True,
                "feature_columns": _build_feature_columns(tags),
                "max_predictions": 5000,
                "min_group_size": 20,
            },
            "experiment_lab": {
                "enabled": True,
                "max_predictions": 5000,
                "bootstrap_iters": 3000,
                "permutation_iters": 5000,
            },
            "meta": {
                "auto_generated": True,
                "priority": priority,
                "reason": str(item.get("reason") or ""),
            },
        }
        specs.append(spec)

    return specs
