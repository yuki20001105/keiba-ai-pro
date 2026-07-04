from __future__ import annotations

from typing import Any


def plan_experiments_from_goal(
    *,
    goal_text: str,
    target_roi: float | None = None,
    scope: str = "all",
) -> dict[str, Any]:
    txt = str(goal_text or "").lower()
    candidates: list[dict[str, Any]] = []

    def add(name: str, priority: int, reason: str, tags: list[str]) -> None:
        candidates.append(
            {
                "name": name,
                "priority": int(priority),
                "reason": reason,
                "tags": tags,
            }
        )

    if any(k in txt for k in ["roi", "回収", "回収率", "profit"]):
        add("feature_popularity_band", 95, "ROI改善に人気帯特徴量が効きやすい", ["feature", "roi", "popularity"])
        add("feature_track_condition", 90, "馬場差分で期待値の歪みを拾える", ["feature", "condition", "surface"])

    if any(k in txt for k in ["短距離", "sprint", "1200", "1400"]):
        add("specialist_sprint_model", 92, "距離限定モデルで分布差を吸収", ["model", "distance", "specialist"])

    if any(k in txt for k in ["安定", "stability", "variance"]):
        add("ensemble_low_variance", 88, "分散低減のため重みアンサンブルを試す", ["ensemble", "stability"])

    if any(k in txt for k in ["校正", "calibration", "probability"]):
        add("calibration_isotonic_vs_platt", 84, "確率校正でEV計算の歪みを減らす", ["calibration", "probability"])

    if not candidates:
        add("baseline_feature_refresh", 75, "ベースライン改善で探索の起点を更新", ["baseline", "feature"])
        add("condition_slice_model", 72, "条件別モデルで局所改善を探索", ["model", "slice"])

    candidates.sort(key=lambda x: int(x.get("priority") or 0), reverse=True)

    return {
        "goal_text": goal_text,
        "target_roi": (float(target_roi) if target_roi is not None else None),
        "scope": scope,
        "plan_items": candidates,
    }
