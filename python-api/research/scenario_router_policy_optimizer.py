from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from mlops import MLOpsStore

from .scenario_router_backtest import ALLOWED_SEGMENTS, run_scenario_router_backtest


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


def _build_row_metrics(
    rows: list[dict[str, Any]],
    *,
    stake_per_race: int,
    winner_by_race: dict[str, str],
) -> tuple[dict[str, Any], dict[str, int]]:
    valid_rows: list[dict[str, Any]] = []
    excluded = {
        "missing_actual": 0,
        "missing_odds": 0,
        "missing_horse": 0,
    }

    for r in rows:
        race_id = str(r.get("race_id") or "")
        horse_id = str(r.get("horse_id") or "")
        odds = r.get("odds")
        winner = winner_by_race.get(race_id)

        if not winner:
            excluded["missing_actual"] += 1
            continue
        if not horse_id:
            excluded["missing_horse"] += 1
            continue
        if odds is None:
            excluded["missing_odds"] += 1
            continue

        odds_f = _safe_float(odds, 0.0)
        hit = bool(horse_id == winner)
        valid_rows.append(
            {
                **r,
                "hit": hit,
                "return_amount": (float(stake_per_race) * odds_f) if hit else 0.0,
            }
        )

    n = len(valid_rows)
    if n <= 0:
        return {
            "races": 0,
            "roi": 0.0,
            "hit_rate": 0.0,
            "valid_rows": [],
        }, excluded

    stake = float(stake_per_race) * float(n)
    ret = sum(float(x.get("return_amount") or 0.0) for x in valid_rows)
    hits = sum(1 for x in valid_rows if bool(x.get("hit")))
    return {
        "races": int(n),
        "roi": ((ret / stake) - 1.0) if stake > 0 else 0.0,
        "hit_rate": float(hits) / float(n),
        "valid_rows": valid_rows,
    }, excluded


def _decide_action(
    *,
    races: int,
    roi_lift: float,
    hit_rate_lift: float,
    fallback_rate: float,
    no_model_rate: float,
    min_races: int,
    min_roi_lift: float,
    min_hit_rate_lift: float,
    disable_if_roi_lift_below: float,
    disable_if_hit_rate_lift_below: float,
    max_fallback_rate: float,
    max_no_model_rate: float,
) -> tuple[str, str]:
    if int(races) < int(min_races):
        return "NEEDS_MORE_DATA", f"races={races} < min_races={min_races}"

    if float(roi_lift) <= float(disable_if_roi_lift_below):
        return "DISABLE", f"roi_lift={roi_lift:.4f} <= disable threshold={disable_if_roi_lift_below:.4f}"

    if float(hit_rate_lift) <= float(disable_if_hit_rate_lift_below):
        return "DISABLE", f"hit_rate_lift={hit_rate_lift:.4f} <= disable threshold={disable_if_hit_rate_lift_below:.4f}"

    strong_roi = max(float(min_roi_lift) * 2.0, float(min_roi_lift) + 0.05)
    healthy_route = (float(fallback_rate) <= float(max_fallback_rate)) and (float(no_model_rate) <= float(max_no_model_rate))

    if float(roi_lift) >= strong_roi and float(hit_rate_lift) >= 0.0 and healthy_route:
        return "RAISE_PRIORITY", "strong and stable lift over global champion"

    if float(roi_lift) >= float(min_roi_lift) and float(hit_rate_lift) >= float(min_hit_rate_lift):
        if healthy_route:
            return "KEEP", "meets minimum lift thresholds"
        return "WATCH", "lift is positive but route quality is unstable (fallback/no_model high)"

    if float(roi_lift) >= 0.0 and float(hit_rate_lift) >= 0.0:
        return "LOWER_PRIORITY", "non-negative but weak lift"

    if float(roi_lift) >= 0.0 or float(hit_rate_lift) >= 0.0:
        return "WATCH", "mixed signal; keep monitoring"

    return "WATCH", "insufficient positive evidence"


def optimize_scenario_router_policies(
    *,
    mlops_db_path: str,
    race_db_path: str,
    date_from: str | None = None,
    date_to: str | None = None,
    target: str | None = None,
    stake_per_race: int = 100,
    scenario_segment_by: list[str] | None = None,
    min_races: int = 30,
    min_roi_lift: float = 0.05,
    min_hit_rate_lift: float = 0.01,
    disable_if_roi_lift_below: float = -0.03,
    disable_if_hit_rate_lift_below: float = -0.02,
    max_fallback_rate: float = 0.40,
    max_no_model_rate: float = 0.05,
    priority_step: int = 10,
    apply_updates: bool = False,
    save_evaluations: bool = True,
    store: MLOpsStore | None = None,
) -> dict[str, Any]:
    db_store = store or MLOpsStore(db_path=Path(mlops_db_path))
    segments = [s for s in (scenario_segment_by or ["expected_pace", "expected_bias", "winning_pattern"]) if s in ALLOWED_SEGMENTS]

    warnings: list[str] = []
    backtest_result = run_scenario_router_backtest(
        mlops_db_path=str(db_store.db_path),
        race_db_path=race_db_path,
        date_from=date_from,
        date_to=date_to,
        target=target,
        stake_per_race=int(stake_per_race),
        scenario_segment_by=segments,
        min_races=int(min_races),
        include_route_type_breakdown=True,
        include_scenario_breakdown=True,
    )
    warnings.extend([str(w) for w in (backtest_result.get("warnings") or [])])

    policies = db_store.list_scenario_model_policies(status="active", target=target)
    if not policies:
        return {
            "summary": {
                "evaluated_policies": 0,
                "keep": 0,
                "raise_priority": 0,
                "lower_priority": 0,
                "disable": 0,
                "needs_more_data": 0,
                "watch": 0,
                "updated_policies": 0,
                "saved_evaluations": 0,
            },
            "actions": [],
            "warnings": [*warnings, "no active scenario policies found"],
            "backtest_summary": backtest_result.get("summary") or {},
        }

    fetch = db_store.fetch_router_backtest_top_predictions(
        date_from=date_from,
        date_to=date_to,
        target=target,
    )
    raw_rows = fetch.get("rows") if isinstance(fetch.get("rows"), list) else []
    missing_routing_count = int(fetch.get("missing_routing_count") or 0)
    if missing_routing_count > 0:
        warnings.append(f"excluded {missing_routing_count} rows without routing metadata")

    if not raw_rows:
        return {
            "summary": {
                "evaluated_policies": 0,
                "keep": 0,
                "raise_priority": 0,
                "lower_priority": 0,
                "disable": 0,
                "needs_more_data": 0,
                "watch": 0,
                "updated_policies": 0,
                "saved_evaluations": 0,
            },
            "actions": [],
            "warnings": [*warnings, "no router rows available for policy optimization"],
            "backtest_summary": backtest_result.get("summary") or {},
        }

    champion_model_id = db_store.get_global_champion_model(target=target)
    if not champion_model_id:
        warnings.append("global champion model not found; policy actions default to NEEDS_MORE_DATA")

    all_race_ids = sorted({str(r.get("race_id") or "") for r in raw_rows if str(r.get("race_id") or "")})
    winner_by_race = _winner_map(race_db_path, all_race_ids)

    evaluations: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []

    for p in policies:
        policy_id = str(p.get("policy_id") or "")
        scenario_key = str(p.get("scenario_key") or "")
        scenario_value = str(p.get("scenario_value") or "")
        model_id = str(p.get("model_id") or "")

        if scenario_key not in segments:
            action = "NEEDS_MORE_DATA"
            reason = f"segment {scenario_key} is not enabled in scenario_segment_by"
            races = 0
            router_roi = 0.0
            global_roi = 0.0
            roi_lift = 0.0
            hit_rate_lift = 0.0
            fallback_rate = 0.0
            no_model_rate = 0.0
        elif not champion_model_id:
            action = "NEEDS_MORE_DATA"
            reason = "global champion model unavailable"
            races = 0
            router_roi = 0.0
            global_roi = 0.0
            roi_lift = 0.0
            hit_rate_lift = 0.0
            fallback_rate = 0.0
            no_model_rate = 0.0
        else:
            seg_rows = [
                r for r in raw_rows
                if str(r.get(scenario_key) or "") == scenario_value
            ]
            seg_total = len(seg_rows)
            fallback_rate = (
                sum(1 for r in seg_rows if bool(r.get("fallback_used"))) / float(seg_total)
                if seg_total > 0 else 0.0
            )
            no_model_rate = (
                sum(1 for r in seg_rows if str(r.get("route_type") or "") == "NO_MODEL") / float(seg_total)
                if seg_total > 0 else 0.0
            )

            router_rows = [
                r for r in raw_rows
                if str(r.get("route_type") or "") == "SEGMENT_SPECIALIST"
                and str(r.get("matched_scenario_key") or "") == scenario_key
                and str(r.get("matched_scenario_value") or "") == scenario_value
                and str(r.get("selected_model_id") or "") == model_id
            ]

            router_metrics, router_excluded = _build_row_metrics(
                router_rows,
                stake_per_race=int(stake_per_race),
                winner_by_race=winner_by_race,
            )

            global_rows = db_store.fetch_top_predictions_for_model_and_races(
                model_id=champion_model_id,
                race_ids=[str(r.get("race_id") or "") for r in router_metrics.get("valid_rows") or []],
                date_from=date_from,
                date_to=date_to,
            )
            global_metrics, global_excluded = _build_row_metrics(
                global_rows,
                stake_per_race=int(stake_per_race),
                winner_by_race=winner_by_race,
            )

            global_by_race = {str(r.get("race_id") or ""): r for r in global_metrics.get("valid_rows") or []}
            router_by_race = {str(r.get("race_id") or ""): r for r in router_metrics.get("valid_rows") or []}
            overlap = sorted(set(router_by_race.keys()) & set(global_by_race.keys()))

            paired_router, _ = _build_row_metrics(
                [router_by_race[rid] for rid in overlap],
                stake_per_race=int(stake_per_race),
                winner_by_race=winner_by_race,
            )
            paired_global, _ = _build_row_metrics(
                [global_by_race[rid] for rid in overlap],
                stake_per_race=int(stake_per_race),
                winner_by_race=winner_by_race,
            )

            races = int(paired_router.get("races") or 0)
            router_roi = float(paired_router.get("roi") or 0.0)
            global_roi = float(paired_global.get("roi") or 0.0)
            roi_lift = router_roi - global_roi
            hit_rate_lift = float(paired_router.get("hit_rate") or 0.0) - float(paired_global.get("hit_rate") or 0.0)

            action, reason = _decide_action(
                races=races,
                roi_lift=roi_lift,
                hit_rate_lift=hit_rate_lift,
                fallback_rate=float(fallback_rate),
                no_model_rate=float(no_model_rate),
                min_races=int(min_races),
                min_roi_lift=float(min_roi_lift),
                min_hit_rate_lift=float(min_hit_rate_lift),
                disable_if_roi_lift_below=float(disable_if_roi_lift_below),
                disable_if_hit_rate_lift_below=float(disable_if_hit_rate_lift_below),
                max_fallback_rate=float(max_fallback_rate),
                max_no_model_rate=float(max_no_model_rate),
            )

            if router_excluded.get("missing_actual") or router_excluded.get("missing_odds") or global_excluded.get("missing_actual") or global_excluded.get("missing_odds"):
                warnings.append(
                    f"policy {policy_id}: excluded rows due to missing actual/odds during paired evaluation"
                )

        rec = {
            "evaluation_id": f"spe_{uuid.uuid4().hex[:16]}",
            "policy_id": policy_id,
            "scenario_key": scenario_key,
            "scenario_value": scenario_value,
            "model_id": model_id,
            "races": int(races),
            "router_roi": float(router_roi),
            "global_roi": float(global_roi),
            "roi_lift": float(roi_lift),
            "hit_rate_lift": float(hit_rate_lift),
            "action": str(action),
            "reason": str(reason),
            "details": {
                "fallback_rate": float(fallback_rate),
                "no_model_rate": float(no_model_rate),
                "target": str(target or ""),
                "date_from": str(date_from or ""),
                "date_to": str(date_to or ""),
                "thresholds": {
                    "min_races": int(min_races),
                    "min_roi_lift": float(min_roi_lift),
                    "min_hit_rate_lift": float(min_hit_rate_lift),
                    "disable_if_roi_lift_below": float(disable_if_roi_lift_below),
                    "disable_if_hit_rate_lift_below": float(disable_if_hit_rate_lift_below),
                    "max_fallback_rate": float(max_fallback_rate),
                    "max_no_model_rate": float(max_no_model_rate),
                },
            },
        }
        evaluations.append(rec)
        actions.append(
            {
                "policy_id": policy_id,
                "scenario_key": scenario_key,
                "scenario_value": scenario_value,
                "model_id": model_id,
                "races": rec["races"],
                "router_roi": rec["router_roi"],
                "global_roi": rec["global_roi"],
                "roi_lift": rec["roi_lift"],
                "hit_rate_lift": rec["hit_rate_lift"],
                "action": rec["action"],
                "reason": rec["reason"],
                "fallback_rate": rec["details"]["fallback_rate"],
                "no_model_rate": rec["details"]["no_model_rate"],
            }
        )

    saved_evals = 0
    if bool(save_evaluations):
        saved_evals = db_store.save_scenario_policy_evaluations(evaluations=evaluations)

    updated = 0
    if bool(apply_updates):
        updates = [a for a in actions if str(a.get("action") or "") in {"RAISE_PRIORITY", "LOWER_PRIORITY", "DISABLE"}]
        updated = db_store.apply_scenario_policy_actions(
            actions=updates,
            priority_step=int(priority_step),
        )

    counts = {
        "KEEP": 0,
        "RAISE_PRIORITY": 0,
        "LOWER_PRIORITY": 0,
        "DISABLE": 0,
        "NEEDS_MORE_DATA": 0,
        "WATCH": 0,
    }
    for a in actions:
        key = str(a.get("action") or "WATCH")
        if key in counts:
            counts[key] += 1

    return {
        "summary": {
            "evaluated_policies": int(len(actions)),
            "keep": int(counts["KEEP"]),
            "raise_priority": int(counts["RAISE_PRIORITY"]),
            "lower_priority": int(counts["LOWER_PRIORITY"]),
            "disable": int(counts["DISABLE"]),
            "needs_more_data": int(counts["NEEDS_MORE_DATA"]),
            "watch": int(counts["WATCH"]),
            "updated_policies": int(updated),
            "saved_evaluations": int(saved_evals),
            "apply_updates": bool(apply_updates),
        },
        "actions": actions,
        "warnings": warnings,
        "backtest_summary": backtest_result.get("summary") or {},
    }