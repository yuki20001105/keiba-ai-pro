from __future__ import annotations

import uuid
from typing import Any

from mlops import MLOpsStore

from .experiment_lab import run_experiment_lab

ALLOWED_SEGMENTS = {"expected_pace", "expected_bias", "winning_pattern"}


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(v)
    except Exception:
        return default


def _bh_fdr(p_values: list[float]) -> list[float]:
    n = len(p_values)
    if n <= 0:
        return []
    indexed = [(i, max(0.0, min(1.0, float(p)))) for i, p in enumerate(p_values)]
    indexed.sort(key=lambda x: x[1])

    q_sorted = [0.0] * n
    running = 1.0
    for rank in range(n, 0, -1):
        idx, p = indexed[rank - 1]
        q = min(running, (p * n) / float(rank))
        running = q
        q_sorted[rank - 1] = q

    out = [1.0] * n
    for rank, (idx, _) in enumerate(indexed):
        out[idx] = max(0.0, min(1.0, q_sorted[rank]))
    return out


def _normalize_segments(v: list[str] | None) -> list[str]:
    out: list[str] = []
    for s in (v or []):
        x = str(s or "").strip()
        if x in ALLOWED_SEGMENTS and x not in out:
            out.append(x)
    return out


def _global_decision(comp: dict[str, Any], policy: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    overlap = _safe_int((comp.get("overlap_test") or {}).get("n_overlap"), 0)
    p_value = _safe_float(((comp.get("overlap_test") or {}).get("permutation") or {}).get("p_value"), 1.0)
    ci_low = _safe_float(((comp.get("overlap_test") or {}).get("bootstrap") or {}).get("ci_low"), 0.0)
    ci_high = _safe_float(((comp.get("overlap_test") or {}).get("bootstrap") or {}).get("ci_high"), 0.0)

    roi_lift = _safe_float((comp.get("delta") or {}).get("roi"), 0.0)
    hit_lift = _safe_float((comp.get("delta") or {}).get("hit_rate"), 0.0)
    top3_lift = _safe_float((comp.get("delta") or {}).get("top3_hit_rate"), 0.0)
    ev_lift = _safe_float((comp.get("delta") or {}).get("expected_value"), 0.0)

    if overlap < _safe_int(policy.get("min_overlap"), 30):
        return (
            "NEEDS_MORE_DATA",
            f"global overlap too small: {overlap}",
            {
                "overlap": overlap,
                "p_value": p_value,
                "ci_low": ci_low,
                "ci_high": ci_high,
                "roi_lift": roi_lift,
                "hit_rate_lift": hit_lift,
                "top3_lift": top3_lift,
                "ev_lift": ev_lift,
            },
        )

    pass_ci = (not bool(policy.get("require_positive_ci_lower", True))) or (ci_low > 0.0)
    pass_global = (
        p_value <= _safe_float(policy.get("alpha"), 0.05)
        and roi_lift >= _safe_float(policy.get("min_roi_lift"), 0.05)
        and hit_lift >= _safe_float(policy.get("min_hit_rate_lift"), 0.02)
        and ev_lift >= _safe_float(policy.get("min_ev_lift"), 0.01)
        and pass_ci
    )

    if pass_global:
        return (
            "PROMOTE_GLOBAL",
            "global metrics show significant positive lift",
            {
                "overlap": overlap,
                "p_value": p_value,
                "ci_low": ci_low,
                "ci_high": ci_high,
                "roi_lift": roi_lift,
                "hit_rate_lift": hit_lift,
                "top3_lift": top3_lift,
                "ev_lift": ev_lift,
            },
        )

    return (
        "REJECT",
        "global lift is not significant under policy thresholds",
        {
            "overlap": overlap,
            "p_value": p_value,
            "ci_low": ci_low,
            "ci_high": ci_high,
            "roi_lift": roi_lift,
            "hit_rate_lift": hit_lift,
            "top3_lift": top3_lift,
            "ev_lift": ev_lift,
        },
    )


def evaluate_scenario_adoption(
    *,
    mlops_db_path: str,
    race_db_path: str,
    baseline_model_id: str,
    challenger_model_id: str,
    scenario_segment_by: list[str] | None = None,
    min_segment_overlap: int = 30,
    alpha: float = 0.05,
    fdr_alpha: float = 0.10,
    min_roi_lift: float = 0.05,
    min_hit_rate_lift: float = 0.02,
    min_ev_lift: float = 0.01,
    require_positive_ci_lower: bool = True,
    max_allowed_global_roi_drop: float = 0.02,
    date_from: str | None = None,
    date_to: str | None = None,
    stake_per_race: int = 100,
    max_predictions: int = 5000,
    bootstrap_iters: int = 3000,
    permutation_iters: int = 5000,
    save_decisions: bool = True,
    save_policies: bool = True,
    experiment_id: str | None = None,
    store: MLOpsStore | None = None,
) -> dict[str, Any]:
    segments = _normalize_segments(scenario_segment_by or ["expected_pace", "expected_bias", "winning_pattern"])
    policy = {
        "min_overlap": int(min_segment_overlap),
        "alpha": float(alpha),
        "fdr_alpha": float(fdr_alpha),
        "min_roi_lift": float(min_roi_lift),
        "min_hit_rate_lift": float(min_hit_rate_lift),
        "min_ev_lift": float(min_ev_lift),
        "require_positive_ci_lower": bool(require_positive_ci_lower),
        "max_allowed_global_roi_drop": float(max_allowed_global_roi_drop),
    }

    lab = run_experiment_lab(
        mlops_db_path=mlops_db_path,
        race_db_path=race_db_path,
        baseline_model_id=baseline_model_id,
        challenger_model_ids=[challenger_model_id],
        date_from=date_from,
        date_to=date_to,
        stake_per_race=int(stake_per_race),
        max_predictions=int(max_predictions),
        bootstrap_iters=int(bootstrap_iters),
        permutation_iters=int(permutation_iters),
        scenario_segment_by=segments,
        min_segment_overlap=int(min_segment_overlap),
    )

    comparisons = lab.get("comparisons") if isinstance(lab.get("comparisons"), list) else []
    if not comparisons:
        return {
            "global_decision": "NEEDS_MORE_DATA",
            "reason": "comparison result is empty",
            "segment_decisions": [],
            "policy": policy,
            "saved": {"decisions": 0, "policies": 0},
            "raw": lab,
        }

    comp = comparisons[0]
    global_decision, global_reason, global_metrics = _global_decision(comp, policy)

    tests = comp.get("scenario_segment_tests") if isinstance(comp.get("scenario_segment_tests"), list) else []

    seg_candidates: list[dict[str, Any]] = []
    for t in tests:
        if not isinstance(t, dict):
            continue
        overlap = _safe_int(t.get("n_overlap"), 0)
        baseline = t.get("baseline") if isinstance(t.get("baseline"), dict) else {}
        challenger = t.get("challenger") if isinstance(t.get("challenger"), dict) else {}
        roi_test = t.get("roi_test") if isinstance(t.get("roi_test"), dict) else {}
        p_value = _safe_float(((roi_test.get("permutation") or {}).get("p_value")), 1.0)

        seg_candidates.append(
            {
                "scenario_key": str(t.get("segment") or ""),
                "scenario_value": str(t.get("value") or ""),
                "overlap": overlap,
                "roi_lift": _safe_float(challenger.get("roi"), 0.0) - _safe_float(baseline.get("roi"), 0.0),
                "hit_rate_lift": _safe_float(challenger.get("hit_rate"), 0.0) - _safe_float(baseline.get("hit_rate"), 0.0),
                "top3_lift": _safe_float(challenger.get("top3_hit_rate"), 0.0) - _safe_float(baseline.get("top3_hit_rate"), 0.0),
                "ev_lift": _safe_float(challenger.get("avg_expected_value"), 0.0) - _safe_float(baseline.get("avg_expected_value"), 0.0),
                "p_value": p_value,
                "ci_lower": _safe_float(((roi_test.get("bootstrap") or {}).get("ci_low")), 0.0),
                "ci_upper": _safe_float(((roi_test.get("bootstrap") or {}).get("ci_high")), 0.0),
                "details": t,
            }
        )

    pvals = [float(x.get("p_value") or 1.0) for x in seg_candidates]
    qvals = _bh_fdr(pvals)

    segment_decisions: list[dict[str, Any]] = []
    promoted_specialist = False
    global_roi_lift = _safe_float((comp.get("delta") or {}).get("roi"), 0.0)

    for i, seg in enumerate(seg_candidates):
        qv = qvals[i] if i < len(qvals) else 1.0
        overlap = _safe_int(seg.get("overlap"), 0)
        roi_lift = _safe_float(seg.get("roi_lift"), 0.0)
        hit_lift = _safe_float(seg.get("hit_rate_lift"), 0.0)
        ev_lift = _safe_float(seg.get("ev_lift"), 0.0)
        ci_lower = _safe_float(seg.get("ci_lower"), 0.0)

        if overlap < _safe_int(policy.get("min_overlap"), 30):
            decision = "NEEDS_MORE_DATA"
            reason = f"overlap too small: {overlap}"
        else:
            pass_ci = (not bool(policy.get("require_positive_ci_lower", True))) or (ci_lower > 0.0)
            pass_global_drop = global_roi_lift >= -_safe_float(policy.get("max_allowed_global_roi_drop"), 0.02)
            pass_rule = (
                float(qv) <= _safe_float(policy.get("fdr_alpha"), 0.10)
                and roi_lift >= _safe_float(policy.get("min_roi_lift"), 0.05)
                and hit_lift >= _safe_float(policy.get("min_hit_rate_lift"), 0.02)
                and ev_lift >= _safe_float(policy.get("min_ev_lift"), 0.01)
                and pass_ci
                and pass_global_drop
            )
            if pass_rule:
                decision = "PROMOTE_SEGMENT_SPECIALIST"
                reason = "segment shows significant and practical lift after FDR correction"
                promoted_specialist = True
            else:
                decision = "REJECT"
                reason = "segment does not satisfy adoption thresholds"

        segment_decisions.append(
            {
                **seg,
                "p_value_fdr": float(qv),
                "decision": decision,
                "reason": reason,
            }
        )

    if global_decision == "PROMOTE_GLOBAL":
        overall_decision = "PROMOTE_GLOBAL"
    elif promoted_specialist:
        overall_decision = "PROMOTE_SEGMENT_SPECIALIST"
    elif any(str(x.get("decision")) == "NEEDS_MORE_DATA" for x in segment_decisions):
        overall_decision = "NEEDS_MORE_DATA"
    else:
        overall_decision = "REJECT"

    decisions_to_save: list[dict[str, Any]] = []
    global_decision_id = f"sad_{uuid.uuid4().hex[:16]}"
    decisions_to_save.append(
        {
            "decision_id": global_decision_id,
            "experiment_id": str(experiment_id or ""),
            "baseline_model_id": baseline_model_id,
            "challenger_model_id": challenger_model_id,
            "scenario_key": "__global__",
            "scenario_value": "all",
            "decision": global_decision,
            "reason": global_reason,
            "roi_lift": global_metrics.get("roi_lift"),
            "hit_rate_lift": global_metrics.get("hit_rate_lift"),
            "top3_lift": global_metrics.get("top3_lift"),
            "ev_lift": global_metrics.get("ev_lift"),
            "p_value": global_metrics.get("p_value"),
            "p_value_fdr": global_metrics.get("p_value"),
            "ci_lower": global_metrics.get("ci_low"),
            "ci_upper": global_metrics.get("ci_high"),
            "overlap": global_metrics.get("overlap"),
            "details": {"policy": policy, "mode": "global"},
        }
    )

    for seg in segment_decisions:
        decisions_to_save.append(
            {
                "decision_id": f"sad_{uuid.uuid4().hex[:16]}",
                "experiment_id": str(experiment_id or ""),
                "baseline_model_id": baseline_model_id,
                "challenger_model_id": challenger_model_id,
                "scenario_key": str(seg.get("scenario_key") or ""),
                "scenario_value": str(seg.get("scenario_value") or ""),
                "decision": str(seg.get("decision") or "REJECT"),
                "reason": str(seg.get("reason") or ""),
                "roi_lift": seg.get("roi_lift"),
                "hit_rate_lift": seg.get("hit_rate_lift"),
                "top3_lift": seg.get("top3_lift"),
                "ev_lift": seg.get("ev_lift"),
                "p_value": seg.get("p_value"),
                "p_value_fdr": seg.get("p_value_fdr"),
                "ci_lower": seg.get("ci_lower"),
                "ci_upper": seg.get("ci_upper"),
                "overlap": seg.get("overlap"),
                "details": seg.get("details") or {},
            }
        )

    policies_to_save: list[dict[str, Any]] = []
    for d in decisions_to_save:
        if str(d.get("decision") or "") != "PROMOTE_SEGMENT_SPECIALIST":
            continue
        policies_to_save.append(
            {
                "policy_id": f"smp_{uuid.uuid4().hex[:16]}",
                "scenario_key": str(d.get("scenario_key") or ""),
                "scenario_value": str(d.get("scenario_value") or ""),
                "model_id": challenger_model_id,
                "feature_set_id": "",
                "strategy_id": "",
                "priority": 50,
                "status": "active",
                "source_decision_id": str(d.get("decision_id") or ""),
                "notes": "auto-generated by scenario adoption gate",
            }
        )

    saved_decisions = 0
    saved_policies = 0
    if save_decisions:
        db_store = store or MLOpsStore()
        saved_decisions = db_store.save_scenario_adoption_decisions(decisions=decisions_to_save)
        if save_policies and policies_to_save:
            saved_policies = db_store.upsert_scenario_model_policies(policies=policies_to_save)

    return {
        "global_decision": overall_decision,
        "global_result": {
            "decision": global_decision,
            "reason": global_reason,
            **global_metrics,
        },
        "segment_decisions": segment_decisions,
        "policy": policy,
        "saved": {
            "decisions": int(saved_decisions),
            "policies": int(saved_policies),
        },
        "source": {
            "baseline_model_id": baseline_model_id,
            "challenger_model_id": challenger_model_id,
            "scenario_segment_by": segments,
        },
    }
