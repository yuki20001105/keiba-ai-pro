from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from mlops import MLOpsStore


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _winner_map(race_db_path: str, race_ids: list[str]) -> dict[str, str]:
    if not race_ids:
        return {}
    conn = sqlite3.connect(race_db_path)
    out: dict[str, str] = {}
    chunk_size = 700
    for i in range(0, len(race_ids), chunk_size):
        chunk = [str(x) for x in race_ids[i : i + chunk_size] if str(x)]
        if not chunk:
            continue
        ph = ",".join(["?"] * len(chunk))
        rows = conn.execute(
            f"SELECT race_id, data FROM race_results_ultimate WHERE race_id IN ({ph})",
            chunk,
        ).fetchall()
        for rid, data in rows:
            race_id = str(rid or "")
            if not race_id or race_id in out:
                continue
            try:
                d = json.loads(data or "{}")
            except Exception:
                continue
            hid = str(d.get("horse_id") or "")
            finish = d.get("finish") or d.get("finish_position")
            try:
                fin = int(float(finish)) if finish is not None else 999
            except Exception:
                fin = 999
            if fin == 1 and hid:
                out[race_id] = hid
    conn.close()
    return out


def _valid_rows(rows: list[dict[str, Any]], *, stake_per_race: int, winner_by_race: dict[str, str]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    valid: list[dict[str, Any]] = []
    excluded = {"missing_actual": 0, "missing_odds": 0, "missing_horse": 0}

    for r in rows:
        race_id = str(r.get("race_id") or "")
        horse_id = str(r.get("horse_id") or "")
        winner = winner_by_race.get(race_id)
        odds = r.get("odds")
        if not winner:
            excluded["missing_actual"] += 1
            continue
        if not horse_id:
            excluded["missing_horse"] += 1
            continue
        if odds is None:
            excluded["missing_odds"] += 1
            continue
        hit = bool(horse_id == winner)
        odds_f = _safe_float(odds, 0.0)
        valid.append(
            {
                **r,
                "hit": hit,
                "return_amount": (float(stake_per_race) * odds_f) if hit else 0.0,
            }
        )

    return valid, excluded


def _summary(rows: list[dict[str, Any]], *, stake_per_race: int) -> dict[str, float]:
    n = len(rows)
    if n <= 0:
        return {"races": 0.0, "roi": 0.0, "hit_rate": 0.0}
    stake = float(stake_per_race) * float(n)
    ret = sum(float(x.get("return_amount") or 0.0) for x in rows)
    hits = sum(1 for x in rows if bool(x.get("hit")))
    return {
        "races": float(n),
        "roi": ((ret / stake) - 1.0) if stake > 0 else 0.0,
        "hit_rate": float(hits) / float(n),
    }


def evaluate_scenario_router_canary(
    *,
    mlops_db_path: str,
    race_db_path: str,
    date_from: str | None = None,
    date_to: str | None = None,
    target: str | None = None,
    min_races: int = 30,
    canary_percent: int | None = None,
    max_fallback_rate: float = 0.50,
    max_no_model_rate: float = 0.05,
    min_roi_lift: float = -0.03,
    min_hit_rate_lift: float = -0.02,
    stake_per_race: int = 100,
    store: MLOpsStore | None = None,
) -> dict[str, Any]:
    db_store = store or MLOpsStore(db_path=Path(mlops_db_path))
    warnings: list[str] = []

    rows = db_store.fetch_canary_evaluation_top_predictions(
        date_from=date_from,
        date_to=date_to,
        target=target,
        canary_percent=canary_percent,
    )
    if not rows:
        return {
            "summary": {
                "canary_active_races": 0,
                "shadow_races": 0,
                "canary_selected_count": 0,
                "canary_active_roi": 0.0,
                "shadow_roi": 0.0,
                "roi_lift": 0.0,
                "hit_rate_lift": 0.0,
                "fallback_rate": 0.0,
                "no_model_rate": 0.0,
                "specialist_usage_rate": 0.0,
            },
            "decision": "NEEDS_MORE_DATA",
            "reason": "no canary prediction rows found",
            "warnings": warnings,
        }

    race_ids = sorted({str(r.get("race_id") or "") for r in rows if str(r.get("race_id") or "")})
    winner_by_race = _winner_map(race_db_path, race_ids)

    active_raw = [r for r in rows if str(r.get("effective_router_mode") or "") == "active"]
    shadow_raw = [r for r in rows if str(r.get("effective_router_mode") or "") == "shadow"]

    active_rows, active_ex = _valid_rows(active_raw, stake_per_race=stake_per_race, winner_by_race=winner_by_race)
    shadow_rows, shadow_ex = _valid_rows(shadow_raw, stake_per_race=stake_per_race, winner_by_race=winner_by_race)

    if active_ex.get("missing_actual") or active_ex.get("missing_odds"):
        warnings.append("canary active rows excluded due to missing actual/odds")
    if shadow_ex.get("missing_actual") or shadow_ex.get("missing_odds"):
        warnings.append("canary shadow rows excluded due to missing actual/odds")

    active_sum = _summary(active_rows, stake_per_race=stake_per_race)
    shadow_sum = _summary(shadow_rows, stake_per_race=stake_per_race)

    active_total = len(active_raw)
    fallback_rate = (
        sum(1 for r in active_raw if bool(r.get("fallback_used"))) / float(active_total)
        if active_total > 0 else 0.0
    )
    no_model_rate = (
        sum(1 for r in active_raw if str(r.get("route_type") or "") == "NO_MODEL") / float(active_total)
        if active_total > 0 else 0.0
    )
    specialist_usage_rate = (
        sum(1 for r in active_raw if str(r.get("route_type") or "") == "SEGMENT_SPECIALIST") / float(active_total)
        if active_total > 0 else 0.0
    )

    roi_lift = float(active_sum.get("roi") or 0.0) - float(shadow_sum.get("roi") or 0.0)
    hit_rate_lift = float(active_sum.get("hit_rate") or 0.0) - float(shadow_sum.get("hit_rate") or 0.0)

    active_count = int(active_sum.get("races") or 0)

    if active_count < int(min_races):
        decision = "NEEDS_MORE_DATA"
        reason = f"canary active races {active_count} < min_races {int(min_races)}"
    elif no_model_rate > float(max_no_model_rate):
        decision = "STOP_CANARY"
        reason = f"no_model_rate {no_model_rate:.4f} > max_no_model_rate {float(max_no_model_rate):.4f}"
    elif fallback_rate > float(max_fallback_rate):
        decision = "HOLD"
        reason = f"fallback_rate {fallback_rate:.4f} > max_fallback_rate {float(max_fallback_rate):.4f}"
    elif roi_lift < float(min_roi_lift):
        decision = "ROLLBACK_TO_SHADOW"
        reason = f"roi_lift {roi_lift:.4f} < min_roi_lift {float(min_roi_lift):.4f}"
    elif hit_rate_lift < float(min_hit_rate_lift):
        decision = "HOLD"
        reason = f"hit_rate_lift {hit_rate_lift:.4f} < min_hit_rate_lift {float(min_hit_rate_lift):.4f}"
    else:
        decision = "INCREASE_CANARY"
        reason = "guardrails passed and canary metrics are acceptable"

    return {
        "summary": {
            "canary_active_races": int(active_sum.get("races") or 0),
            "shadow_races": int(shadow_sum.get("races") or 0),
            "canary_selected_count": sum(1 for r in rows if bool(r.get("canary_selected"))),
            "canary_active_roi": float(active_sum.get("roi") or 0.0),
            "shadow_roi": float(shadow_sum.get("roi") or 0.0),
            "roi_lift": float(roi_lift),
            "hit_rate_lift": float(hit_rate_lift),
            "fallback_rate": float(fallback_rate),
            "no_model_rate": float(no_model_rate),
            "specialist_usage_rate": float(specialist_usage_rate),
        },
        "decision": decision,
        "reason": reason,
        "warnings": warnings,
    }
