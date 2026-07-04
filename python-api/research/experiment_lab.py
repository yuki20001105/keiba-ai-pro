from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from typing import Any

from .statistical_test import bootstrap_mean_diff, paired_win_rate, permutation_test_mean


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _chunked(xs: list[str], n: int = 800) -> list[list[str]]:
    out: list[list[str]] = []
    step = max(1, int(n))
    for i in range(0, len(xs), step):
        out.append(xs[i : i + step])
    return out


def _winner_map(conn_race: sqlite3.Connection, race_ids: list[str]) -> dict[str, str]:
    winners: dict[str, str] = {}
    for chunk in _chunked(race_ids, 600):
        ph = ",".join(["?"] * len(chunk))
        rows = conn_race.execute(
            f"SELECT race_id, data FROM race_results_ultimate WHERE race_id IN ({ph})",
            chunk,
        ).fetchall()
        for rid, data in rows:
            try:
                d = json.loads(data or "{}")
            except Exception:
                continue
            hid = str(d.get("horse_id") or "")
            finish = int(_safe_float(d.get("finish") or d.get("finish_position"), 999))
            if finish == 1 and hid and str(rid) not in winners:
                winners[str(rid)] = hid
    return winners


def _top3_hit_by_prediction(
    conn_mlops: sqlite3.Connection,
    prediction_to_race: dict[str, str],
    winners: dict[str, str],
) -> dict[str, int]:
    if not prediction_to_race:
        return {}
    out: dict[str, int] = {pid: 0 for pid in prediction_to_race.keys()}
    ids = list(prediction_to_race.keys())
    for chunk in _chunked(ids, 600):
        ph = ",".join(["?"] * len(chunk))
        rows = conn_mlops.execute(
            f"""
            SELECT prediction_id, horse_id
            FROM prediction_results
            WHERE prediction_id IN ({ph}) AND rank <= 3
            """,
            chunk,
        ).fetchall()
        for pid, horse_id in rows:
            p = str(pid or "")
            hid = str(horse_id or "")
            rid = prediction_to_race.get(p, "")
            if p and rid and hid and winners.get(rid) == hid:
                out[p] = 1
    return out


def _latest_model_meta(conn: sqlite3.Connection, model_id: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT created_at, target, stage, feature_quality_score, metrics_json
        FROM model_registry
        WHERE model_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (model_id,),
    ).fetchone()
    if not row:
        return {
            "model_id": model_id,
            "target": "",
            "stage": "",
            "feature_quality_score": None,
            "auc": 0.0,
            "created_at": "",
        }
    metrics = {}
    try:
        metrics = json.loads(row[4] or "{}")
    except Exception:
        metrics = {}
    auc = _safe_float(metrics.get("auc") or metrics.get("cv_auc_mean"), 0.0)
    return {
        "model_id": model_id,
        "target": str(row[1] or ""),
        "stage": str(row[2] or ""),
        "feature_quality_score": (float(row[3]) if row[3] is not None else None),
        "auc": auc,
        "created_at": str(row[0] or ""),
    }


def _model_returns(
    *,
    conn_mlops: sqlite3.Connection,
    conn_race: sqlite3.Connection,
    model_id: str,
    date_from: str | None,
    date_to: str | None,
    stake_per_race: int,
    max_predictions: int,
    scenario_segment_by: list[str] | None = None,
) -> dict[str, Any]:
    where = ["pr.model_id = ?", "rs.rank = 1"]
    params: list[Any] = [model_id]
    if date_from:
        where.append("pr.race_date >= ?")
        params.append(date_from)
    if date_to:
        where.append("pr.race_date <= ?")
        params.append(date_to)

    segment_cols: list[str] = []
    for seg in (scenario_segment_by or []):
        s = str(seg or "").strip()
        if s in {"expected_pace", "expected_bias", "winning_pattern"}:
            segment_cols.append(s)

    segment_select = ""
    if segment_cols:
        segment_select = ", " + ", ".join([f"pr.{c}" for c in segment_cols])

    sql = """
        SELECT pr.prediction_id, pr.race_id, pr.race_date,
               rs.horse_id, rs.odds, rs.expected_value{segment_select}
        FROM prediction_runs pr
        JOIN prediction_results rs ON rs.prediction_id = pr.prediction_id
        WHERE {where}
        ORDER BY pr.created_at DESC
        LIMIT ?
    """.format(where=" AND ".join(where), segment_select=segment_select)
    params.append(int(max_predictions))

    rows = conn_mlops.execute(sql, params).fetchall()
    if not rows:
        return {
            "rows": [],
            "returns_by_race": {},
            "summary": {
                "model_id": model_id,
                "n_predictions": 0,
                "hit_rate": 0.0,
                "roi": 0.0,
                "avg_expected_value": 0.0,
                "total_stake": 0,
                "total_return": 0.0,
            },
        }

    race_ids = sorted({str(r[1]) for r in rows if r[1] is not None})
    winners = _winner_map(conn_race, race_ids)
    prediction_to_race = {
        str(r[0] or ""): str(r[1] or "")
        for r in rows
        if r and r[0] is not None and r[1] is not None
    }
    top3_hit_map = _top3_hit_by_prediction(conn_mlops, prediction_to_race, winners)

    items: list[dict[str, Any]] = []
    returns_by_race: dict[str, float] = {}
    hits_by_race: dict[str, float] = {}
    top3_hits_by_race: dict[str, float] = {}
    ev_by_race: dict[str, float] = {}
    segment_maps: dict[str, dict[str, dict[str, float]]] = defaultdict(
        lambda: {
            "returns": {},
            "hits": {},
            "top3_hits": {},
            "ev": {},
        }
    )
    for row in rows:
        pid, rid, rdate, hid, odds, ev = row[:6]
        seg_vals = row[6:]
        prediction_id = str(pid or "")
        race_id = str(rid or "")
        horse_id = str(hid or "")
        is_hit = bool(race_id and horse_id and winners.get(race_id) == horse_id)
        top3_hit = int(top3_hit_map.get(prediction_id, 0))
        odds_f = _safe_float(odds, 0.0)
        ret = float(stake_per_race) * odds_f if is_hit else 0.0
        row = {
            "prediction_id": prediction_id,
            "race_id": race_id,
            "race_date": str(rdate or ""),
            "horse_id": horse_id,
            "odds": odds_f,
            "expected_value": _safe_float(ev, 0.0),
            "hit": is_hit,
            "top3_hit": top3_hit,
            "return_amount": float(ret),
        }
        if segment_cols and seg_vals:
            row["segments"] = {
                segment_cols[i]: str(seg_vals[i] or "")
                for i in range(min(len(segment_cols), len(seg_vals)))
            }
        items.append(row)
        if race_id:
            returns_by_race[race_id] = float(ret)
            hits_by_race[race_id] = 1.0 if is_hit else 0.0
            top3_hits_by_race[race_id] = float(top3_hit)
            ev_by_race[race_id] = float(row.get("expected_value") or 0.0)
            if segment_cols and row.get("segments"):
                segs = row.get("segments") if isinstance(row.get("segments"), dict) else {}
                for seg_col in segment_cols:
                    seg_val = str(segs.get(seg_col) or "unknown")
                    k = f"{seg_col}:{seg_val}"
                    segment_maps[k]["returns"][race_id] = float(ret)
                    segment_maps[k]["hits"][race_id] = 1.0 if is_hit else 0.0
                    segment_maps[k]["top3_hits"][race_id] = float(top3_hit)
                    segment_maps[k]["ev"][race_id] = float(row.get("expected_value") or 0.0)

    n = len(items)
    total_stake = int(stake_per_race) * n
    total_return = sum(float(x.get("return_amount") or 0.0) for x in items)
    hits = sum(1 for x in items if bool(x.get("hit")))
    top3_hits = sum(int(x.get("top3_hit") or 0) for x in items)
    avg_ev = sum(float(x.get("expected_value") or 0.0) for x in items) / float(max(1, n))

    segment_summary: dict[str, dict[str, Any]] = {}
    if segment_cols:
        agg: dict[str, dict[str, Any]] = defaultdict(lambda: {
            "n": 0,
            "hits": 0,
            "top3_hits": 0,
            "stake": 0.0,
            "ret": 0.0,
            "ev_sum": 0.0,
        })
        for it in items:
            segs = it.get("segments") if isinstance(it.get("segments"), dict) else {}
            for seg_col in segment_cols:
                seg_val = str(segs.get(seg_col) or "unknown")
                k = f"{seg_col}:{seg_val}"
                g = agg[k]
                g["n"] += 1
                if bool(it.get("hit")):
                    g["hits"] += 1
                if bool(it.get("top3_hit")):
                    g["top3_hits"] += 1
                g["stake"] += float(stake_per_race)
                g["ret"] += float(it.get("return_amount") or 0.0)
                g["ev_sum"] += float(it.get("expected_value") or 0.0)

        for k, g in agg.items():
            n_seg = int(g["n"])
            if n_seg <= 0:
                continue
            seg_col, seg_val = k.split(":", 1)
            stake_seg = float(g["stake"])
            segment_summary[k] = {
                "segment": seg_col,
                "value": seg_val,
                "n_predictions": n_seg,
                "hit_rate": (float(g["hits"]) / float(n_seg)) if n_seg > 0 else 0.0,
                "top3_hit_rate": (float(g.get("top3_hits") or 0.0) / float(n_seg)) if n_seg > 0 else 0.0,
                "roi": ((float(g["ret"]) / stake_seg) - 1.0) if stake_seg > 0 else 0.0,
                "avg_expected_value": float(g["ev_sum"]) / float(max(1, n_seg)),
            }

    return {
        "rows": items,
        "returns_by_race": returns_by_race,
        "summary": {
            "model_id": model_id,
            "n_predictions": int(n),
            "hit_rate": float(hits) / float(n) if n > 0 else 0.0,
            "top3_hit_rate": float(top3_hits) / float(n) if n > 0 else 0.0,
            "roi": ((float(total_return) / float(total_stake)) - 1.0) if total_stake > 0 else 0.0,
            "avg_expected_value": float(avg_ev),
            "total_stake": int(total_stake),
            "total_return": float(total_return),
        },
        "scenario_segments": segment_summary,
        "segment_maps": segment_maps,
        "hits_by_race": hits_by_race,
        "top3_hits_by_race": top3_hits_by_race,
        "ev_by_race": ev_by_race,
    }


def _compare_metric_on_overlap(
    base_map: dict[str, float],
    challenger_map: dict[str, float],
    *,
    bootstrap_iters: int,
    permutation_iters: int,
) -> dict[str, Any]:
    keys = sorted(set(base_map.keys()) & set(challenger_map.keys()))
    if not keys:
        return {
            "n_overlap": 0,
            "baseline_mean": 0.0,
            "challenger_mean": 0.0,
            "mean_diff": 0.0,
            "bootstrap": {"mean_diff": 0.0, "ci_low": 0.0, "ci_high": 0.0, "iterations": 0},
            "permutation": {"p_value": 1.0, "observed_diff": 0.0, "iterations": 0},
            "paired_win_rate": {"n": 0, "challenger_win_rate": 0.0},
        }

    base = [float(base_map[k]) for k in keys]
    chal = [float(challenger_map[k]) for k in keys]
    boot = bootstrap_mean_diff(base, chal, iterations=bootstrap_iters)
    perm = permutation_test_mean(base, chal, iterations=permutation_iters)
    pwr = paired_win_rate(base, chal)
    mean_b = sum(base) / float(len(base)) if base else 0.0
    mean_c = sum(chal) / float(len(chal)) if chal else 0.0
    return {
        "n_overlap": int(len(keys)),
        "baseline_mean": float(mean_b),
        "challenger_mean": float(mean_c),
        "mean_diff": float(mean_c - mean_b),
        "bootstrap": boot,
        "permutation": perm,
        "paired_win_rate": pwr,
    }


def _scenario_segment_significance(
    baseline_eval: dict[str, Any],
    challenger_eval: dict[str, Any],
    *,
    bootstrap_iters: int,
    permutation_iters: int,
    min_segment_overlap: int,
) -> list[dict[str, Any]]:
    base_maps = baseline_eval.get("segment_maps") if isinstance(baseline_eval.get("segment_maps"), dict) else {}
    chal_maps = challenger_eval.get("segment_maps") if isinstance(challenger_eval.get("segment_maps"), dict) else {}
    base_seg = baseline_eval.get("scenario_segments") if isinstance(baseline_eval.get("scenario_segments"), dict) else {}
    chal_seg = challenger_eval.get("scenario_segments") if isinstance(challenger_eval.get("scenario_segments"), dict) else {}

    keys = sorted(set(base_maps.keys()) & set(chal_maps.keys()))
    out: list[dict[str, Any]] = []
    for key in keys:
        bm = base_maps.get(key) if isinstance(base_maps.get(key), dict) else {}
        cm = chal_maps.get(key) if isinstance(chal_maps.get(key), dict) else {}

        roi_test = _compare_on_overlap(
            bm.get("returns") or {},
            cm.get("returns") or {},
            bootstrap_iters=bootstrap_iters,
            permutation_iters=permutation_iters,
        )
        n_overlap = int(roi_test.get("n_overlap") or 0)
        if n_overlap < int(min_segment_overlap):
            continue

        hit_test = _compare_metric_on_overlap(
            bm.get("hits") or {},
            cm.get("hits") or {},
            bootstrap_iters=bootstrap_iters,
            permutation_iters=permutation_iters,
        )
        top3_test = _compare_metric_on_overlap(
            bm.get("top3_hits") or {},
            cm.get("top3_hits") or {},
            bootstrap_iters=bootstrap_iters,
            permutation_iters=permutation_iters,
        )
        ev_test = _compare_metric_on_overlap(
            bm.get("ev") or {},
            cm.get("ev") or {},
            bootstrap_iters=bootstrap_iters,
            permutation_iters=permutation_iters,
        )

        seg_col, seg_val = key.split(":", 1)
        roi_p = float(((roi_test.get("permutation") or {}).get("p_value") or 1.0))
        out.append(
            {
                "segment": seg_col,
                "value": seg_val,
                "n_overlap": n_overlap,
                "baseline": base_seg.get(key) or {},
                "challenger": chal_seg.get(key) or {},
                "roi_test": {
                    **roi_test,
                    "significant_improvement": bool(roi_p < 0.05 and float(roi_test.get("mean_return_diff") or 0.0) > 0.0),
                },
                "hit_rate_test": hit_test,
                "top3_hit_rate_test": top3_test,
                "expected_value_test": ev_test,
            }
        )

    out.sort(
        key=lambda x: (
            bool(((x.get("roi_test") or {}).get("significant_improvement") or False)),
            float(((x.get("roi_test") or {}).get("mean_return_diff") or 0.0)),
            float(((x.get("expected_value_test") or {}).get("mean_diff") or 0.0)),
        ),
        reverse=True,
    )
    return out


def _compare_on_overlap(
    base_map: dict[str, float],
    challenger_map: dict[str, float],
    *,
    bootstrap_iters: int,
    permutation_iters: int,
) -> dict[str, Any]:
    keys = sorted(set(base_map.keys()) & set(challenger_map.keys()))
    if not keys:
        return {
            "n_overlap": 0,
            "baseline_mean_return": 0.0,
            "challenger_mean_return": 0.0,
            "mean_return_diff": 0.0,
            "bootstrap": {"mean_diff": 0.0, "ci_low": 0.0, "ci_high": 0.0, "iterations": 0},
            "permutation": {"p_value": 1.0, "observed_diff": 0.0, "iterations": 0},
            "paired_win_rate": {"n": 0, "challenger_win_rate": 0.0},
        }

    base = [float(base_map[k]) for k in keys]
    chal = [float(challenger_map[k]) for k in keys]

    boot = bootstrap_mean_diff(base, chal, iterations=bootstrap_iters)
    perm = permutation_test_mean(base, chal, iterations=permutation_iters)
    pwr = paired_win_rate(base, chal)

    mean_b = sum(base) / float(len(base)) if base else 0.0
    mean_c = sum(chal) / float(len(chal)) if chal else 0.0

    return {
        "n_overlap": int(len(keys)),
        "baseline_mean_return": float(mean_b),
        "challenger_mean_return": float(mean_c),
        "mean_return_diff": float(mean_c - mean_b),
        "bootstrap": boot,
        "permutation": perm,
        "paired_win_rate": pwr,
    }


def run_experiment_lab(
    *,
    mlops_db_path: str,
    race_db_path: str,
    baseline_model_id: str,
    challenger_model_ids: list[str],
    date_from: str | None = None,
    date_to: str | None = None,
    stake_per_race: int = 100,
    max_predictions: int = 5000,
    bootstrap_iters: int = 3000,
    permutation_iters: int = 5000,
    scenario_segment_by: list[str] | None = None,
    min_segment_overlap: int = 20,
) -> dict[str, Any]:
    challengers = [m.strip() for m in challenger_model_ids if m and m.strip()]
    if not baseline_model_id or not challengers:
        return {
            "baseline": {},
            "comparisons": [],
            "leaderboard": [],
            "message": "baseline_model_id and challenger_model_ids are required",
        }

    conn_m = sqlite3.connect(mlops_db_path)
    conn_r = sqlite3.connect(race_db_path)

    baseline_meta = _latest_model_meta(conn_m, baseline_model_id)
    baseline_eval = _model_returns(
        conn_mlops=conn_m,
        conn_race=conn_r,
        model_id=baseline_model_id,
        date_from=date_from,
        date_to=date_to,
        stake_per_race=int(stake_per_race),
        max_predictions=int(max_predictions),
        scenario_segment_by=scenario_segment_by,
    )

    comparisons: list[dict[str, Any]] = []
    leaderboard: list[dict[str, Any]] = []
    scenario_leaderboard_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)

    baseline_summary = {
        **baseline_meta,
        **(baseline_eval.get("summary") or {}),
    }
    for skey, sval in (baseline_eval.get("scenario_segments") or {}).items():
        scenario_leaderboard_rows[str(skey)].append(
            {
                "model_id": baseline_model_id,
                "role": "baseline",
                **(sval if isinstance(sval, dict) else {}),
            }
        )
    leaderboard.append({
        **baseline_summary,
        "role": "baseline",
        "vs_baseline": {
            "n_overlap": 0,
            "mean_return_diff": 0.0,
            "p_value": 1.0,
            "significant": False,
        },
    })

    for model_id in challengers:
        c_meta = _latest_model_meta(conn_m, model_id)
        c_eval = _model_returns(
            conn_mlops=conn_m,
            conn_race=conn_r,
            model_id=model_id,
            date_from=date_from,
            date_to=date_to,
            stake_per_race=int(stake_per_race),
            max_predictions=int(max_predictions),
            scenario_segment_by=scenario_segment_by,
        )
        overlap = _compare_on_overlap(
            baseline_eval.get("returns_by_race") or {},
            c_eval.get("returns_by_race") or {},
            bootstrap_iters=int(bootstrap_iters),
            permutation_iters=int(permutation_iters),
        )
        p_value = float(((overlap.get("permutation") or {}).get("p_value") or 1.0))
        significant = bool(p_value < 0.05 and float(overlap.get("mean_return_diff") or 0.0) > 0.0)

        comp = {
            "baseline_model_id": baseline_model_id,
            "challenger_model_id": model_id,
            "baseline": baseline_summary,
            "challenger": {
                **c_meta,
                **(c_eval.get("summary") or {}),
            },
            "overlap_test": {
                **overlap,
                "significant_improvement": significant,
            },
            "delta": {
                "auc": float(c_meta.get("auc") or 0.0) - float(baseline_meta.get("auc") or 0.0),
                "roi": float((c_eval.get("summary") or {}).get("roi") or 0.0)
                - float((baseline_eval.get("summary") or {}).get("roi") or 0.0),
                "hit_rate": float((c_eval.get("summary") or {}).get("hit_rate") or 0.0)
                - float((baseline_eval.get("summary") or {}).get("hit_rate") or 0.0),
                "top3_hit_rate": float((c_eval.get("summary") or {}).get("top3_hit_rate") or 0.0)
                - float((baseline_eval.get("summary") or {}).get("top3_hit_rate") or 0.0),
                "expected_value": float((c_eval.get("summary") or {}).get("avg_expected_value") or 0.0)
                - float((baseline_eval.get("summary") or {}).get("avg_expected_value") or 0.0),
            },
            "scenario_segments": {
                "baseline": baseline_eval.get("scenario_segments") or {},
                "challenger": c_eval.get("scenario_segments") or {},
            },
            "scenario_segment_tests": _scenario_segment_significance(
                baseline_eval,
                c_eval,
                bootstrap_iters=int(bootstrap_iters),
                permutation_iters=int(permutation_iters),
                min_segment_overlap=int(min_segment_overlap),
            ),
        }
        comparisons.append(comp)

        for skey, sval in (c_eval.get("scenario_segments") or {}).items():
            scenario_leaderboard_rows[str(skey)].append(
                {
                    "model_id": model_id,
                    "role": "challenger",
                    **(sval if isinstance(sval, dict) else {}),
                }
            )

        leaderboard.append({
            **comp["challenger"],
            "role": "challenger",
            "vs_baseline": {
                "n_overlap": int(overlap.get("n_overlap") or 0),
                "mean_return_diff": float(overlap.get("mean_return_diff") or 0.0),
                "p_value": p_value,
                "significant": significant,
            },
        })

    conn_m.close()
    conn_r.close()

    comparisons.sort(
        key=lambda x: (
            bool(((x.get("overlap_test") or {}).get("significant_improvement") or False)),
            float(((x.get("delta") or {}).get("roi") or 0.0)),
            float(((x.get("delta") or {}).get("auc") or 0.0)),
        ),
        reverse=True,
    )
    leaderboard.sort(
        key=lambda x: (
            bool(((x.get("vs_baseline") or {}).get("significant") or False)),
            float(x.get("roi") or 0.0),
            float(x.get("auc") or 0.0),
        ),
        reverse=True,
    )

    scenario_leaderboards: list[dict[str, Any]] = []
    for skey, rows in scenario_leaderboard_rows.items():
        segment, value = skey.split(":", 1) if ":" in skey else ("segment", skey)
        baseline_row = next((r for r in rows if str(r.get("model_id") or "") == baseline_model_id), None)
        base_roi = float((baseline_row or {}).get("roi") or 0.0)
        base_hit = float((baseline_row or {}).get("hit_rate") or 0.0)
        base_top3 = float((baseline_row or {}).get("top3_hit_rate") or 0.0)
        base_ev = float((baseline_row or {}).get("avg_expected_value") or 0.0)

        ranked = []
        for r in rows:
            ranked.append(
                {
                    **r,
                    "vs_baseline": {
                        "roi_diff": float(r.get("roi") or 0.0) - base_roi,
                        "hit_rate_diff": float(r.get("hit_rate") or 0.0) - base_hit,
                        "top3_hit_rate_diff": float(r.get("top3_hit_rate") or 0.0) - base_top3,
                        "expected_value_diff": float(r.get("avg_expected_value") or 0.0) - base_ev,
                    },
                }
            )

        ranked.sort(
            key=lambda x: (
                float(x.get("roi") or 0.0),
                float(x.get("hit_rate") or 0.0),
                float(x.get("avg_expected_value") or 0.0),
            ),
            reverse=True,
        )
        scenario_leaderboards.append(
            {
                "segment": segment,
                "value": value,
                "n_models": int(len(ranked)),
                "items": ranked,
            }
        )

    scenario_leaderboards.sort(
        key=lambda x: (
            int(x.get("n_models") or 0),
            int(((x.get("items") or [{}])[0]).get("n_predictions") or 0),
        ),
        reverse=True,
    )

    return {
        "baseline": baseline_summary,
        "comparisons": comparisons,
        "leaderboard": leaderboard,
        "scenario_leaderboards": scenario_leaderboards,
        "settings": {
            "stake_per_race": int(stake_per_race),
            "max_predictions": int(max_predictions),
            "date_from": date_from,
            "date_to": date_to,
            "bootstrap_iters": int(bootstrap_iters),
            "permutation_iters": int(permutation_iters),
            "scenario_segment_by": scenario_segment_by or [],
            "min_segment_overlap": int(min_segment_overlap),
        },
    }
