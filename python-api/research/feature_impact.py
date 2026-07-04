from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from typing import Any


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _season_of(race_date: str) -> str:
    s = str(race_date or "")
    if len(s) < 6:
        return "unknown"
    try:
        m = int(s[4:6])
    except Exception:
        return "unknown"
    if m in (3, 4, 5):
        return "spring"
    if m in (6, 7, 8):
        return "summer"
    if m in (9, 10, 11):
        return "autumn"
    return "winter"


def _distance_bucket(v: Any) -> str:
    d = int(_safe_float(v, 0.0))
    if d <= 0:
        return "unknown"
    if d <= 1400:
        return "sprint"
    if d <= 1800:
        return "mile"
    if d <= 2200:
        return "middle"
    return "long"


def _popularity_band(v: Any) -> str:
    p = int(_safe_float(v, 0.0))
    if p <= 0:
        return "unknown"
    if p <= 3:
        return "fav_1_3"
    if p <= 6:
        return "mid_4_6"
    if p <= 10:
        return "outer_7_10"
    return "longshot_11_plus"


def _build_condition_row(meta: dict[str, Any], horse: dict[str, Any], race_date: str) -> dict[str, str]:
    return {
        "venue": str(meta.get("venue") or "unknown"),
        "surface": str(meta.get("track_type") or meta.get("surface") or "unknown"),
        "distance_bucket": _distance_bucket(meta.get("distance")),
        "field_condition": str(meta.get("field_condition") or "unknown"),
        "race_class": str(meta.get("race_class") or "unknown"),
        "season": _season_of(race_date),
        "popularity_band": _popularity_band(horse.get("popularity")),
    }


def _aggregate_metrics(rows: list[dict[str, Any]], stake_per_race: int) -> dict[str, Any]:
    n = len(rows)
    if n <= 0:
        return {
            "n": 0,
            "hit_rate": 0.0,
            "roi": 0.0,
            "avg_expected_value": 0.0,
            "top3_rate": 0.0,
            "total_stake": 0,
            "total_return": 0.0,
        }

    total_hits = sum(1 for r in rows if bool(r.get("top1_hit")))
    total_top3 = sum(1 for r in rows if bool(r.get("top3_hit")))
    total_stake = int(stake_per_race) * n
    total_return = sum(_safe_float(r.get("return_amount"), 0.0) for r in rows)
    avg_ev = sum(_safe_float(r.get("expected_value"), 0.0) for r in rows) / float(n)
    return {
        "n": n,
        "hit_rate": float(total_hits) / float(n),
        "top3_rate": float(total_top3) / float(n),
        "roi": ((float(total_return) / float(total_stake)) - 1.0) if total_stake > 0 else 0.0,
        "avg_expected_value": float(avg_ev),
        "total_stake": int(total_stake),
        "total_return": float(total_return),
    }


def _quantile_bucket(values: list[float], v: float) -> str:
    if not values:
        return "unknown"
    xs = sorted(values)
    n = len(xs)
    q1 = xs[max(0, min(n - 1, int((n - 1) * 0.33)))]
    q2 = xs[max(0, min(n - 1, int((n - 1) * 0.66)))]
    if v <= q1:
        return "low"
    if v <= q2:
        return "mid"
    return "high"


def run_feature_impact_analysis(
    *,
    mlops_db_path: str,
    race_db_path: str,
    model_ids: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    stake_per_race: int = 100,
    feature_columns: list[str] | None = None,
    max_predictions: int = 5000,
    min_group_size: int = 20,
) -> dict[str, Any]:
    where = []
    params: list[Any] = []
    if model_ids:
        ph = ",".join(["?"] * len(model_ids))
        where.append(f"pr.model_id IN ({ph})")
        params.extend(model_ids)
    if date_from:
        where.append("pr.race_date >= ?")
        params.append(date_from)
    if date_to:
        where.append("pr.race_date <= ?")
        params.append(date_to)

    sql = """
        SELECT pr.prediction_id, pr.race_id, pr.model_id, pr.race_date,
               rs.horse_id, rs.rank, rs.calibrated_probability, rs.expected_value, rs.odds
        FROM prediction_runs pr
        JOIN prediction_results rs ON rs.prediction_id = pr.prediction_id
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY pr.created_at DESC, rs.rank ASC"

    conn = sqlite3.connect(mlops_db_path)
    rows = conn.execute(sql, params).fetchall()
    conn.close()

    if not rows:
        return {
            "summary": {
                "n_predictions": 0,
                "hit_rate": 0.0,
                "top3_rate": 0.0,
                "roi": 0.0,
                "avg_expected_value": 0.0,
            },
            "by_condition": {},
            "feature_impact": [],
        }

    by_pred: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        pid, race_id, model_id, race_date, horse_id, rank, cprob, ev, odds = r
        by_pred[str(pid)].append(
            {
                "prediction_id": str(pid),
                "race_id": str(race_id),
                "model_id": str(model_id),
                "race_date": str(race_date or ""),
                "horse_id": str(horse_id or ""),
                "rank": int(rank or 999),
                "calibrated_probability": _safe_float(cprob),
                "expected_value": _safe_float(ev),
                "odds": _safe_float(odds),
            }
        )

    top_preds: list[dict[str, Any]] = []
    for plist in by_pred.values():
        plist.sort(key=lambda x: int(x.get("rank") or 999))
        top = plist[0]
        top_preds.append(top)
        if len(top_preds) >= int(max_predictions):
            break

    race_ids = sorted({str(x["race_id"]) for x in top_preds if x.get("race_id")})
    if not race_ids:
        return {
            "summary": {
                "n_predictions": 0,
                "hit_rate": 0.0,
                "top3_rate": 0.0,
                "roi": 0.0,
                "avg_expected_value": 0.0,
            },
            "by_condition": {},
            "feature_impact": [],
        }

    conn_r = sqlite3.connect(race_db_path)

    meta_map: dict[str, dict[str, Any]] = {}
    winner_map: dict[str, str] = {}
    top3_map: dict[str, set[str]] = defaultdict(set)
    horse_map: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)

    for rid in race_ids:
        row = conn_r.execute("SELECT data FROM races_ultimate WHERE race_id = ?", (rid,)).fetchone()
        if row and row[0]:
            try:
                meta_map[rid] = json.loads(row[0] or "{}")
            except Exception:
                meta_map[rid] = {}
        else:
            meta_map[rid] = {}

        hrows = conn_r.execute("SELECT data FROM race_results_ultimate WHERE race_id = ?", (rid,)).fetchall()
        for hr in hrows:
            try:
                d = json.loads(hr[0] or "{}")
            except Exception:
                continue
            hid = str(d.get("horse_id") or "")
            if not hid:
                continue
            horse_map[rid][hid] = d
            finish = int(_safe_float(d.get("finish") or d.get("finish_position"), 999))
            if finish == 1:
                winner_map[rid] = hid
            if finish <= 3:
                top3_map[rid].add(hid)

    conn_r.close()

    analysis_rows: list[dict[str, Any]] = []
    for p in top_preds:
        rid = str(p.get("race_id") or "")
        hid = str(p.get("horse_id") or "")
        win_hid = winner_map.get(rid, "")
        is_hit = bool(win_hid and hid and hid == win_hid)
        is_top3 = bool(hid and hid in top3_map.get(rid, set()))
        odds = _safe_float(p.get("odds"), 0.0)
        ret = (int(stake_per_race) * odds) if is_hit else 0.0

        hrow = horse_map.get(rid, {}).get(hid, {})
        cond = _build_condition_row(meta_map.get(rid, {}), hrow, str(p.get("race_date") or ""))

        analysis_rows.append(
            {
                **p,
                **cond,
                "top1_hit": is_hit,
                "top3_hit": is_top3,
                "return_amount": float(ret),
            }
        )

    overall = _aggregate_metrics(analysis_rows, stake_per_race=int(stake_per_race))

    condition_keys = [
        "venue",
        "distance_bucket",
        "surface",
        "field_condition",
        "race_class",
        "season",
        "popularity_band",
    ]

    by_condition: dict[str, list[dict[str, Any]]] = {}
    for key in condition_keys:
        group_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in analysis_rows:
            group_map[str(r.get(key) or "unknown")].append(r)

        items = []
        for label, grows in group_map.items():
            if len(grows) < int(min_group_size):
                continue
            m = _aggregate_metrics(grows, stake_per_race=int(stake_per_race))
            items.append(
                {
                    key: label,
                    **m,
                    "roi_lift_vs_all": float(m.get("roi", 0.0)) - float(overall.get("roi", 0.0)),
                    "hit_lift_vs_all": float(m.get("hit_rate", 0.0)) - float(overall.get("hit_rate", 0.0)),
                }
            )
        items.sort(key=lambda x: float(x.get("roi", 0.0)), reverse=True)
        by_condition[key] = items

    feature_impact: list[dict[str, Any]] = []
    cols = [c.strip() for c in (feature_columns or []) if str(c).strip()]
    for col in cols:
        values: list[float] = []
        for r in analysis_rows:
            rid = str(r.get("race_id") or "")
            hid = str(r.get("horse_id") or "")
            hrow = horse_map.get(rid, {}).get(hid, {})
            v = hrow.get(col)
            if v is None:
                continue
            fv = _safe_float(v, float("nan"))
            if fv == fv:
                values.append(fv)

        if len(values) < int(min_group_size * 2):
            continue

        bucket_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in analysis_rows:
            rid = str(r.get("race_id") or "")
            hid = str(r.get("horse_id") or "")
            hrow = horse_map.get(rid, {}).get(hid, {})
            v = hrow.get(col)
            if v is None:
                continue
            fv = _safe_float(v, float("nan"))
            if fv != fv:
                continue
            b = _quantile_bucket(values, fv)
            bucket_rows[b].append(r)

        buckets = []
        for bname, grows in bucket_rows.items():
            if len(grows) < int(min_group_size):
                continue
            m = _aggregate_metrics(grows, stake_per_race=int(stake_per_race))
            buckets.append(
                {
                    "bucket": bname,
                    **m,
                    "roi_lift_vs_all": float(m.get("roi", 0.0)) - float(overall.get("roi", 0.0)),
                    "hit_lift_vs_all": float(m.get("hit_rate", 0.0)) - float(overall.get("hit_rate", 0.0)),
                }
            )
        buckets.sort(key=lambda x: float(x.get("roi", 0.0)), reverse=True)
        if not buckets:
            continue

        feature_impact.append(
            {
                "feature": col,
                "buckets": buckets,
                "best_bucket": buckets[0],
            }
        )

    feature_impact.sort(key=lambda x: float((x.get("best_bucket") or {}).get("roi_lift_vs_all", 0.0)), reverse=True)

    return {
        "summary": {
            "n_predictions": int(len(analysis_rows)),
            **overall,
        },
        "by_condition": by_condition,
        "feature_impact": feature_impact,
    }
