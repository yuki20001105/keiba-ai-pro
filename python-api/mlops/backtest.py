from __future__ import annotations

import sqlite3
from typing import Any


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def run_backtest(
    *,
    mlops_db_path: str,
    race_db_path: str,
    model_ids: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    stake_per_race: int = 100,
) -> dict[str, Any]:
    conn = sqlite3.connect(mlops_db_path)

    where = []
    params: list[Any] = []
    if model_ids:
        placeholders = ",".join(["?"] * len(model_ids))
        where.append(f"pr.model_id IN ({placeholders})")
        params.extend(model_ids)
    if date_from:
        where.append("pr.race_date >= ?")
        params.append(date_from)
    if date_to:
        where.append("pr.race_date <= ?")
        params.append(date_to)

    sql = """
        SELECT pr.prediction_id, pr.race_id, pr.model_id, pr.race_date,
               rs.horse_id, rs.rank, rs.probability, rs.calibrated_probability, rs.expected_value, rs.odds
        FROM prediction_runs pr
        JOIN prediction_results rs ON rs.prediction_id = pr.prediction_id
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY pr.created_at DESC, rs.rank ASC"

    rows = conn.execute(sql, params).fetchall()
    conn.close()

    if not rows:
        return {
            "summary": {
                "n_predictions": 0,
                "n_races": 0,
                "hit_rate": 0.0,
                "roi": 0.0,
                "avg_expected_value": 0.0,
            },
            "by_model": [],
        }

    by_pred: dict[str, dict[str, Any]] = {}
    for r in rows:
        pid, race_id, model_id, race_date, hid, rank, prob, cprob, ev, odds = r
        key = str(pid)
        if key not in by_pred:
            by_pred[key] = {
                "prediction_id": str(pid),
                "race_id": str(race_id),
                "model_id": str(model_id),
                "race_date": str(race_date or ""),
                "rows": [],
            }
        by_pred[key]["rows"].append(
            {
                "horse_id": str(hid or ""),
                "rank": int(rank or 999),
                "probability": _safe_float(prob),
                "calibrated_probability": _safe_float(cprob),
                "expected_value": _safe_float(ev),
                "odds": _safe_float(odds),
            }
        )

    conn_r = sqlite3.connect(race_db_path)

    by_model: dict[str, dict[str, Any]] = {}
    for pred in by_pred.values():
        model_id = str(pred["model_id"])
        if model_id not in by_model:
            by_model[model_id] = {
                "model_id": model_id,
                "n_predictions": 0,
                "n_hits": 0,
                "total_stake": 0,
                "total_return": 0.0,
                "sum_ev": 0.0,
            }

        rows_pred = sorted(pred["rows"], key=lambda x: int(x["rank"]))
        top = rows_pred[0] if rows_pred else None
        if top is None:
            continue

        winner_rows = conn_r.execute(
            "SELECT data FROM race_results_ultimate WHERE race_id = ?",
            (pred["race_id"],),
        ).fetchall()

        winner_horse_id = ""
        for wr in winner_rows:
            import json
            try:
                d = json.loads(wr[0] or "{}")
                finish = d.get("finish") or d.get("finish_position")
                if finish is not None and int(finish) == 1:
                    winner_horse_id = str(d.get("horse_id") or "")
                    break
            except Exception:
                continue

        bm = by_model[model_id]
        bm["n_predictions"] += 1
        bm["total_stake"] += int(stake_per_race)
        bm["sum_ev"] += _safe_float(top.get("expected_value"), 0.0)
        if winner_horse_id and str(top.get("horse_id") or "") == winner_horse_id:
            bm["n_hits"] += 1
            bm["total_return"] += int(stake_per_race) * _safe_float(top.get("odds"), 0.0)

    conn_r.close()

    items: list[dict[str, Any]] = []
    total_preds = 0
    total_hits = 0
    total_stake = 0
    total_return = 0.0
    sum_ev = 0.0

    for model_id, bm in by_model.items():
        n = int(bm["n_predictions"])
        hits = int(bm["n_hits"])
        stake = int(bm["total_stake"])
        ret = float(bm["total_return"])
        avg_ev = (float(bm["sum_ev"]) / float(n)) if n > 0 else 0.0
        hit_rate = (float(hits) / float(n)) if n > 0 else 0.0
        roi = ((ret / float(stake)) - 1.0) if stake > 0 else 0.0
        items.append(
            {
                "model_id": model_id,
                "n_predictions": n,
                "hit_rate": hit_rate,
                "roi": roi,
                "avg_expected_value": avg_ev,
                "total_stake": stake,
                "total_return": ret,
            }
        )
        total_preds += n
        total_hits += hits
        total_stake += stake
        total_return += ret
        sum_ev += float(bm["sum_ev"])

    items.sort(key=lambda x: (float(x["roi"]), float(x["hit_rate"])), reverse=True)

    return {
        "summary": {
            "n_predictions": total_preds,
            "n_races": len(by_pred),
            "hit_rate": (float(total_hits) / float(total_preds)) if total_preds > 0 else 0.0,
            "roi": ((float(total_return) / float(total_stake)) - 1.0) if total_stake > 0 else 0.0,
            "avg_expected_value": (float(sum_ev) / float(total_preds)) if total_preds > 0 else 0.0,
            "total_stake": total_stake,
            "total_return": total_return,
        },
        "by_model": items,
    }
