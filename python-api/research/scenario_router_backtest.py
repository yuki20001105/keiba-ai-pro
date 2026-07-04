from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from mlops import MLOpsStore

ALLOWED_SEGMENTS = {"expected_pace", "expected_bias", "winning_pattern"}


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


def _top1_metrics(rows: list[dict[str, Any]], *, stake_per_race: int, winner_by_race: dict[str, str]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    valid: list[dict[str, Any]] = []
    counters = {
        "excluded_missing_actual": 0,
        "excluded_missing_odds": 0,
        "excluded_missing_horse": 0,
    }
    for r in rows:
        race_id = str(r.get("race_id") or "")
        horse_id = str(r.get("horse_id") or "")
        winner = winner_by_race.get(race_id)
        odds = r.get("odds")

        if not winner:
            counters["excluded_missing_actual"] += 1
            continue
        if not horse_id:
            counters["excluded_missing_horse"] += 1
            continue
        if odds is None:
            counters["excluded_missing_odds"] += 1
            continue

        odds_f = _safe_float(odds, 0.0)
        is_hit = bool(horse_id == winner)
        ret = (float(stake_per_race) * odds_f) if is_hit else 0.0
        valid.append(
            {
                **r,
                "hit": is_hit,
                "return_amount": float(ret),
                "expected_value": _safe_float(r.get("expected_value"), 0.0),
                "odds": odds_f,
            }
        )
    return valid, counters


def _summary(rows: list[dict[str, Any]], *, stake_per_race: int, top3_hit_by_pred: dict[str, int]) -> dict[str, Any]:
    n = len(rows)
    if n <= 0:
        return {
            "races": 0,
            "roi": 0.0,
            "hit_rate": 0.0,
            "top3_hit_rate": 0.0,
            "avg_expected_value": 0.0,
        }

    stake = float(stake_per_race) * float(n)
    ret = sum(float(x.get("return_amount") or 0.0) for x in rows)
    hits = sum(1 for x in rows if bool(x.get("hit")))
    top3_hits = sum(int(top3_hit_by_pred.get(str(x.get("prediction_id") or ""), 0)) for x in rows)
    ev = sum(float(x.get("expected_value") or 0.0) for x in rows)

    return {
        "races": int(n),
        "roi": ((ret / stake) - 1.0) if stake > 0 else 0.0,
        "hit_rate": float(hits) / float(n),
        "top3_hit_rate": float(top3_hits) / float(n),
        "avg_expected_value": float(ev) / float(n),
    }


def run_scenario_router_backtest(
    *,
    mlops_db_path: str,
    race_db_path: str,
    date_from: str | None = None,
    date_to: str | None = None,
    target: str | None = None,
    router_mode: str = "active",
    stake_per_race: int = 100,
    scenario_segment_by: list[str] | None = None,
    min_races: int = 30,
    include_route_type_breakdown: bool = True,
    include_scenario_breakdown: bool = True,
) -> dict[str, Any]:
    store = MLOpsStore(db_path=Path(mlops_db_path))
    mode = str(router_mode or "active").strip().lower()
    if mode not in {"active", "shadow"}:
        mode = "active"
    segments = [s for s in (scenario_segment_by or ["expected_pace", "expected_bias", "winning_pattern"]) if s in ALLOWED_SEGMENTS]
    warnings: list[str] = []

    fetch = store.fetch_router_backtest_top_predictions(
        date_from=date_from,
        date_to=date_to,
        target=target,
        router_mode=mode,
    )
    raw_rows = fetch.get("rows") if isinstance(fetch.get("rows"), list) else []
    missing_routing_count = int(fetch.get("missing_routing_count") or 0)

    if missing_routing_count > 0:
        warnings.append(f"excluded {missing_routing_count} prediction_runs without routing metadata")

    if not raw_rows:
        return {
            "summary": {
                "global_roi": 0.0,
                "router_roi": 0.0,
                "roi_lift": 0.0,
                "global_hit_rate": 0.0,
                "router_hit_rate": 0.0,
                "hit_rate_lift": 0.0,
                "top3_hit_rate_lift": 0.0,
                "avg_expected_value_lift": 0.0,
                "specialist_usage_rate": 0.0,
                "fallback_rate": 0.0,
                "no_model_rate": 0.0,
            },
            "by_route_type": [],
            "by_scenario": [],
            "warnings": [*warnings, "no router prediction rows found"],
        }

    route_rows = [r for r in raw_rows if str(r.get("route_type") or "")]
    no_model_rows = [r for r in route_rows if str(r.get("route_type") or "") == "NO_MODEL"]
    if mode == "shadow":
        route_rows = [
            {
                **r,
                "route_type": str(r.get("shadow_route_type") or ""),
                "selected_model_id": str(r.get("shadow_selected_model_id") or ""),
                "matched_scenario_key": str(r.get("shadow_matched_scenario_key") or ""),
                "matched_scenario_value": str(r.get("shadow_matched_scenario_value") or ""),
                "router_reason": str(r.get("shadow_router_reason") or ""),
                "fallback_used": bool(r.get("shadow_fallback_used")),
            }
            for r in raw_rows
            if str(r.get("shadow_route_type") or "")
        ]
        no_model_rows = [r for r in route_rows if str(r.get("route_type") or "") == "NO_MODEL"]

    race_ids = sorted({str(r.get("race_id") or "") for r in route_rows if str(r.get("race_id") or "")})
    winner_by_race = _winner_map(race_db_path, race_ids)

    router_valid_all, router_excluded = _top1_metrics(route_rows, stake_per_race=stake_per_race, winner_by_race=winner_by_race)
    router_valid = [r for r in router_valid_all if str(r.get("route_type") or "") != "NO_MODEL"]

    if router_excluded.get("excluded_missing_actual"):
        warnings.append(f"excluded {router_excluded['excluded_missing_actual']} rows due to missing actual winner")
    if router_excluded.get("excluded_missing_odds"):
        warnings.append(f"excluded {router_excluded['excluded_missing_odds']} rows due to missing odds")
    if router_excluded.get("excluded_missing_horse"):
        warnings.append(f"excluded {router_excluded['excluded_missing_horse']} rows due to missing horse_id")

    total_route = len(route_rows)
    specialist_usage_rate = (sum(1 for r in route_rows if str(r.get("route_type") or "") == "SEGMENT_SPECIALIST") / float(total_route)) if total_route > 0 else 0.0
    fallback_rate = (sum(1 for r in route_rows if bool(r.get("fallback_used"))) / float(total_route)) if total_route > 0 else 0.0
    no_model_rate = (len(no_model_rows) / float(total_route)) if total_route > 0 else 0.0

    # Baseline/Router comparison
    if mode == "shadow":
        # Baseline: actual prediction rows in shadow mode
        global_rows_valid, _global_excluded = _top1_metrics(
            raw_rows,
            stake_per_race=stake_per_race,
            winner_by_race=winner_by_race,
        )
        # Router side: virtual selected model rows (if available in registry)
        pairs = [
            (str(r.get("selected_model_id") or ""), str(r.get("race_id") or ""))
            for r in router_valid
            if str(r.get("selected_model_id") or "") and str(r.get("race_id") or "")
        ]
        virtual_rows = store.fetch_top_predictions_for_model_race_pairs(
            model_race_pairs=pairs,
            date_from=date_from,
            date_to=date_to,
        )
        if len(virtual_rows) < len(pairs):
            warnings.append(
                f"shadow virtual predictions missing for {max(0, len(pairs) - len(virtual_rows))} races"
            )
        router_virtual_valid, virtual_excluded = _top1_metrics(
            virtual_rows,
            stake_per_race=stake_per_race,
            winner_by_race=winner_by_race,
        )
        if virtual_excluded.get("excluded_missing_actual") or virtual_excluded.get("excluded_missing_odds"):
            warnings.append("shadow virtual rows excluded due to missing actual/odds")
        global_by_race = {str(r.get("race_id") or ""): r for r in global_rows_valid}
        router_by_race = {str(r.get("race_id") or ""): r for r in router_virtual_valid}
    else:
        champion_model = store.get_global_champion_model(target=target)
        if not champion_model:
            warnings.append("global champion model not found; using zeros for global comparison")
            global_rows_valid = []
        else:
            global_rows = store.fetch_top_predictions_for_model_and_races(
                model_id=champion_model,
                race_ids=[str(r.get("race_id") or "") for r in router_valid],
                date_from=date_from,
                date_to=date_to,
            )
            global_rows_valid, global_excluded = _top1_metrics(global_rows, stake_per_race=stake_per_race, winner_by_race=winner_by_race)
            dropped_overlap = max(0, len(router_valid) - len(global_rows_valid))
            if dropped_overlap > 0:
                warnings.append(f"global champion predictions missing for {dropped_overlap} router races")
            if global_excluded.get("excluded_missing_actual") or global_excluded.get("excluded_missing_odds") or global_excluded.get("excluded_missing_horse"):
                warnings.append(
                    "global champion rows excluded due to missing actual/odds/horse"
                )
        global_by_race = {str(r.get("race_id") or ""): r for r in global_rows_valid}
        router_by_race = {str(r.get("race_id") or ""): r for r in router_valid}

    # Paired overlap only
    overlap_races = sorted(set(router_by_race.keys()) & set(global_by_race.keys()))

    router_overlap_rows = [router_by_race[rid] for rid in overlap_races]
    global_overlap_rows = [global_by_race[rid] for rid in overlap_races]

    pred_ids_for_top3 = [str(r.get("prediction_id") or "") for r in [*router_overlap_rows, *global_overlap_rows, *router_valid_all] if str(r.get("prediction_id") or "")]
    top3_map = store.fetch_top3_hit_flags(prediction_ids=pred_ids_for_top3, winner_by_race=winner_by_race)

    router_summary = _summary(router_overlap_rows, stake_per_race=stake_per_race, top3_hit_by_pred=top3_map)
    global_summary = _summary(global_overlap_rows, stake_per_race=stake_per_race, top3_hit_by_pred=top3_map)

    by_route_type: list[dict[str, Any]] = []
    if include_route_type_breakdown:
        route_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in router_valid_all:
            rt = str(r.get("route_type") or "")
            route_groups[rt].append(r)
        for rt in ["SEGMENT_SPECIALIST", "GLOBAL_CHAMPION", "FALLBACK_GLOBAL", "NO_MODEL"]:
            rows_rt = route_groups.get(rt) or []
            by_route_type.append({
                "route_type": rt,
                **_summary(rows_rt, stake_per_race=stake_per_race, top3_hit_by_pred=top3_map),
            })

    by_scenario: list[dict[str, Any]] = []
    if include_scenario_breakdown and overlap_races:
        for seg in segments:
            grouped_router: dict[str, list[dict[str, Any]]] = defaultdict(list)
            grouped_global: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for rid in overlap_races:
                rr = router_by_race[rid]
                gv = global_by_race[rid]
                val = str(rr.get(seg) or "unknown")
                grouped_router[val].append(rr)
                grouped_global[val].append(gv)
            for val, rr_list in grouped_router.items():
                if len(rr_list) < int(min_races):
                    continue
                gg_list = grouped_global.get(val) or []
                if len(gg_list) < int(min_races):
                    continue
                rs = _summary(rr_list, stake_per_race=stake_per_race, top3_hit_by_pred=top3_map)
                gs = _summary(gg_list, stake_per_race=stake_per_race, top3_hit_by_pred=top3_map)
                by_scenario.append(
                    {
                        "scenario_key": seg,
                        "scenario_value": val,
                        "races": int(rs.get("races") or 0),
                        "router_roi": float(rs.get("roi") or 0.0),
                        "global_roi": float(gs.get("roi") or 0.0),
                        "roi_lift": float(rs.get("roi") or 0.0) - float(gs.get("roi") or 0.0),
                    }
                )

    by_scenario.sort(key=lambda x: (float(x.get("roi_lift") or 0.0), int(x.get("races") or 0)), reverse=True)

    return {
        "summary": {
            "router_mode": mode,
            "global_roi": float(global_summary.get("roi") or 0.0),
            "router_roi": float(router_summary.get("roi") or 0.0),
            "roi_lift": float(router_summary.get("roi") or 0.0) - float(global_summary.get("roi") or 0.0),
            "global_hit_rate": float(global_summary.get("hit_rate") or 0.0),
            "router_hit_rate": float(router_summary.get("hit_rate") or 0.0),
            "hit_rate_lift": float(router_summary.get("hit_rate") or 0.0) - float(global_summary.get("hit_rate") or 0.0),
            "top3_hit_rate_lift": float(router_summary.get("top3_hit_rate") or 0.0) - float(global_summary.get("top3_hit_rate") or 0.0),
            "avg_expected_value_lift": float(router_summary.get("avg_expected_value") or 0.0) - float(global_summary.get("avg_expected_value") or 0.0),
            "specialist_usage_rate": float(specialist_usage_rate),
            "fallback_rate": float(fallback_rate),
            "no_model_rate": float(no_model_rate),
        },
        "by_route_type": by_route_type,
        "by_scenario": by_scenario,
        "warnings": warnings,
    }
