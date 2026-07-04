from __future__ import annotations

from typing import Any

from .experiment_planner import plan_experiments_from_goal
from .knowledge_base import ResearchKnowledgeBase


def recommend_next_experiments(
    *,
    goal_text: str,
    baseline_model_id: str,
    challenger_model_ids: list[str],
    limit: int = 10,
    kb: ResearchKnowledgeBase | None = None,
) -> dict[str, Any]:
    knowledge = kb or ResearchKnowledgeBase()
    plan = plan_experiments_from_goal(goal_text=goal_text)
    signals = knowledge.list_signals(metric="roi", limit=200)

    family_score: dict[str, float] = {}
    for s in signals:
        fam = str(s.get("feature_family") or "other")
        lift = float(s.get("lift") or 0.0)
        conf = float(s.get("confidence") or 0.0)
        family_score[fam] = family_score.get(fam, 0.0) + (lift * conf)

    recs: list[dict[str, Any]] = []
    for item in (plan.get("plan_items") or []):
        if not isinstance(item, dict):
            continue
        tags = [str(x) for x in (item.get("tags") or [])]
        families = []
        for t in tags:
            if t in {"popularity", "market"}:
                families.append("market")
            elif t in {"distance", "condition", "surface"}:
                families.append("course")
            elif t in {"weight"}:
                families.append("weight")
            elif t in {"speed"}:
                families.append("speed")
            elif t in {"jockey", "trainer", "human"}:
                families.append("human")
            elif t in {"model", "ensemble", "calibration"}:
                families.append("model")

        kb_boost = sum(float(family_score.get(f, 0.0)) for f in families)
        score = float(item.get("priority") or 0.0) + kb_boost
        recs.append(
            {
                "name": str(item.get("name") or ""),
                "reason": str(item.get("reason") or ""),
                "tags": tags,
                "families": families,
                "kb_boost": kb_boost,
                "score": score,
                "models": {
                    "baseline_model_id": baseline_model_id,
                    "challenger_model_ids": challenger_model_ids,
                },
            }
        )

    recs.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
    top = recs[: max(1, min(int(limit), 100))]

    for r in top:
        knowledge.add_recommendation(goal_text=goal_text, recommendation=r, score=float(r.get("score") or 0.0))

    return {
        "goal_text": goal_text,
        "family_score": family_score,
        "recommendations": top,
    }
