from __future__ import annotations

from typing import Any

from app_config import _ensure_model_local, get_active_model_id, get_latest_model
from knowledge.scenario_engine import get_race_scenario
from mlops import MLOpsStore


def _model_available(model_id: str) -> bool:
    if not model_id:
        return False
    return _ensure_model_local(model_id) is not None


def _resolve_global_fallback_model(*, store: MLOpsStore, target: str | None) -> str:
    candidate = store.get_global_champion_model(target=target) or ""
    if candidate and _model_available(candidate):
        return candidate

    active_id = get_active_model_id()
    if active_id and _model_available(active_id):
        return active_id

    latest_model = get_latest_model()
    if latest_model and _model_available(latest_model.stem):
        return latest_model.stem

    return ""


def resolve_scenario_model(
    *,
    race_id: str,
    race_db_path: str,
    target: str | None = None,
    use_router: bool = True,
    store: MLOpsStore | None = None,
) -> dict[str, Any]:
    db_store = store or MLOpsStore()
    scenario_payload = get_race_scenario(
        race_db_path=race_db_path,
        race_id=str(race_id),
        auto_rebuild_if_missing=True,
    )

    scenario = {
        "expected_pace": str(scenario_payload.get("expected_pace") or ""),
        "expected_bias": str(scenario_payload.get("expected_bias") or ""),
        "winning_pattern": str(scenario_payload.get("winning_pattern") or ""),
    }

    if not bool(use_router):
        return {
            "race_id": str(race_id),
            "scenario": scenario,
            "selected_model_id": "",
            "route_type": "NO_MODEL",
            "matched_policy": None,
            "fallback_used": False,
            "router_reason": "scenario router disabled",
        }

    policies = db_store.find_scenario_model_policies(scenario=scenario, status="active")
    best_policy = policies[0] if policies else None

    global_model = _resolve_global_fallback_model(store=db_store, target=target)

    if best_policy:
        specialist_model = str(best_policy.get("model_id") or "")
        if _model_available(specialist_model):
            return {
                "race_id": str(race_id),
                "scenario": scenario,
                "selected_model_id": specialist_model,
                "route_type": "SEGMENT_SPECIALIST",
                "matched_policy": best_policy,
                "fallback_used": False,
                "router_reason": (
                    f"matched active policy {best_policy.get('scenario_key')}="
                    f"{best_policy.get('scenario_value')}"
                ),
            }

        # Policy exists but specialist model is unavailable -> fallback.
        if global_model:
            return {
                "race_id": str(race_id),
                "scenario": scenario,
                "selected_model_id": global_model,
                "route_type": "FALLBACK_GLOBAL",
                "matched_policy": best_policy,
                "fallback_used": True,
                "router_reason": "specialist model unavailable; fallback to global champion",
            }

        return {
            "race_id": str(race_id),
            "scenario": scenario,
            "selected_model_id": "",
            "route_type": "NO_MODEL",
            "matched_policy": best_policy,
            "fallback_used": True,
            "router_reason": "specialist unavailable and no global model found",
        }

    if global_model:
        return {
            "race_id": str(race_id),
            "scenario": scenario,
            "selected_model_id": global_model,
            "route_type": "GLOBAL_CHAMPION",
            "matched_policy": None,
            "fallback_used": True,
            "router_reason": "no active specialist policy; using global champion",
        }

    return {
        "race_id": str(race_id),
        "scenario": scenario,
        "selected_model_id": "",
        "route_type": "NO_MODEL",
        "matched_policy": None,
        "fallback_used": True,
        "router_reason": "no specialist policy and no global model available",
    }
