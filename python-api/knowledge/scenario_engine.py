from __future__ import annotations

import json
import hashlib
import math
import sqlite3
from pathlib import Path
from typing import Any

from .pace_model import analyze_race_pace, rebuild_pace_profiles
from .track_bias import analyze_track_bias, rebuild_track_bias_profiles


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_knowledge_db() -> Path:
    return _repo_root() / "keiba" / "data" / "knowledge.db"


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(float(v))
    except Exception:
        return default


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS race_scenarios (
            race_id TEXT PRIMARY KEY,
            expected_pace TEXT NOT NULL,
            expected_bias TEXT NOT NULL,
            race_complexity TEXT NOT NULL,
            winning_pattern TEXT NOT NULL,
            front_collapse_probability REAL NOT NULL,
            inside_advantage REAL NOT NULL,
            outside_advantage REAL NOT NULL,
            stalker_advantage REAL NOT NULL,
            closer_advantage REAL NOT NULL,
            recommended_styles_json TEXT NOT NULL,
            expected_front_count INTEGER NOT NULL,
            pace_pressure_index REAL NOT NULL,
            source_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scenario_meta (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            updated_at TEXT NOT NULL,
            race_count INTEGER NOT NULL
        )
        """
    )


def _now() -> str:
    from datetime import datetime

    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _scenario_from_bias_payload(payload: dict[str, Any]) -> dict[str, Any]:
    expected_pace = str(payload.get("expected_pace") or "unknown")
    expected_bias = str(payload.get("expected_bias") or "neutral")

    front_bias = _safe_float(payload.get("front_bias"), 0.0)
    stalker_bias = _safe_float(payload.get("stalker_bias"), 0.0)
    closer_bias = _safe_float(payload.get("closer_bias"), 0.0)

    inside = _safe_float(payload.get("inside_bias"), 0.0)
    outside = _safe_float(payload.get("outside_bias"), 0.0)

    pace_pressure = _safe_float(payload.get("pace_pressure_index"), 0.0)
    front_count = _safe_int(payload.get("front_runner_count_est"), 0)

    pace_factor = 1.2 if expected_pace == "fast" else (-0.7 if expected_pace == "slow" else 0.2)
    collapse_raw = (closer_bias - front_bias) * 2.8 + pace_factor + (pace_pressure - 0.33) * 3.0
    front_collapse_probability = _sigmoid(collapse_raw)

    style_scores = {
        "front": front_bias + (0.10 if expected_pace == "slow" else -0.06),
        "stalker": stalker_bias + (0.05 if expected_pace == "moderate" else 0.0),
        "closer": closer_bias + (0.12 if expected_pace == "fast" else -0.05),
    }
    style_rank = sorted(style_scores.items(), key=lambda x: x[1], reverse=True)
    recommended_styles = [style_rank[0][0], style_rank[1][0]] if len(style_rank) >= 2 else [style_rank[0][0]]
    winning_pattern = recommended_styles[0]

    complexity_score = (
        abs(inside - outside) * 2.2
        + abs(front_bias - closer_bias) * 2.0
        + abs(pace_pressure - 0.33) * 1.8
    )
    if complexity_score >= 1.15:
        race_complexity = "high"
    elif complexity_score >= 0.65:
        race_complexity = "medium"
    else:
        race_complexity = "low"

    return {
        "expected_pace": expected_pace,
        "expected_bias": expected_bias,
        "race_complexity": race_complexity,
        "winning_pattern": winning_pattern,
        "front_collapse_probability": float(front_collapse_probability),
        "inside_advantage": float(inside),
        "outside_advantage": float(outside),
        "stalker_advantage": float(stalker_bias),
        "closer_advantage": float(closer_bias),
        "recommended_styles": recommended_styles,
        "expected_front_count": int(front_count),
        "pace_pressure_index": float(pace_pressure),
        "source": payload,
    }


def rebuild_race_scenarios(
    *,
    race_db_path: str,
    knowledge_db_path: str | None = None,
    max_races: int = 0,
    rebuild_dependencies: bool = True,
) -> dict[str, Any]:
    kdb = Path(knowledge_db_path) if knowledge_db_path else _default_knowledge_db()

    if rebuild_dependencies:
        rebuild_pace_profiles(race_db_path=race_db_path, knowledge_db_path=str(kdb))
        rebuild_track_bias_profiles(race_db_path=race_db_path, knowledge_db_path=str(kdb))

    conn_r = sqlite3.connect(race_db_path)
    race_rows = conn_r.execute("SELECT race_id FROM races_ultimate ORDER BY race_id DESC").fetchall()
    conn_r.close()

    race_ids = [str(r[0]) for r in race_rows if r and r[0] is not None]
    if max_races > 0:
        race_ids = race_ids[: int(max_races)]

    conn_k = sqlite3.connect(str(kdb))
    conn_k.execute("PRAGMA journal_mode=WAL")
    _ensure_schema(conn_k)

    written = 0
    for race_id in race_ids:
        bias_payload = analyze_track_bias(
            race_db_path=race_db_path,
            race_id=race_id,
            knowledge_db_path=str(kdb),
            auto_rebuild_if_empty=False,
        )
        if str(bias_payload.get("message") or "") == "race_not_found":
            continue
        scen = _scenario_from_bias_payload(bias_payload)
        conn_k.execute(
            """
            INSERT OR REPLACE INTO race_scenarios (
                race_id, expected_pace, expected_bias, race_complexity, winning_pattern,
                front_collapse_probability, inside_advantage, outside_advantage,
                stalker_advantage, closer_advantage, recommended_styles_json,
                expected_front_count, pace_pressure_index, source_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                race_id,
                str(scen["expected_pace"]),
                str(scen["expected_bias"]),
                str(scen["race_complexity"]),
                str(scen["winning_pattern"]),
                float(scen["front_collapse_probability"]),
                float(scen["inside_advantage"]),
                float(scen["outside_advantage"]),
                float(scen["stalker_advantage"]),
                float(scen["closer_advantage"]),
                json.dumps(scen.get("recommended_styles") or [], ensure_ascii=False),
                int(scen["expected_front_count"]),
                float(scen["pace_pressure_index"]),
                json.dumps(scen.get("source") or {}, ensure_ascii=False, default=str),
                _now(),
            ),
        )
        written += 1

    conn_k.execute(
        """
        INSERT OR REPLACE INTO scenario_meta (id, updated_at, race_count)
        VALUES (1, ?, ?)
        """,
        (_now(), int(written)),
    )
    conn_k.commit()
    conn_k.close()

    return {
        "race_count": int(written),
        "max_races": int(max_races),
    }


def get_race_scenario(
    *,
    race_db_path: str,
    race_id: str,
    knowledge_db_path: str | None = None,
    auto_rebuild_if_missing: bool = True,
) -> dict[str, Any]:
    kdb = Path(knowledge_db_path) if knowledge_db_path else _default_knowledge_db()

    conn_k = sqlite3.connect(str(kdb))
    conn_k.execute("PRAGMA journal_mode=WAL")
    _ensure_schema(conn_k)

    row = conn_k.execute(
        """
        SELECT race_id, expected_pace, expected_bias, race_complexity, winning_pattern,
               front_collapse_probability, inside_advantage, outside_advantage,
               stalker_advantage, closer_advantage, recommended_styles_json,
               expected_front_count, pace_pressure_index, source_json
        FROM race_scenarios
        WHERE race_id = ?
        """,
        (race_id,),
    ).fetchone()

    if row is None and auto_rebuild_if_missing:
        conn_k.close()
        # On-demand build for single race using pace+bias analyzers
        bias_payload = analyze_track_bias(
            race_db_path=race_db_path,
            race_id=race_id,
            knowledge_db_path=str(kdb),
            auto_rebuild_if_empty=True,
        )
        if str(bias_payload.get("message") or "") == "race_not_found":
            return {"race_id": race_id, "message": "race_not_found"}
        scen = _scenario_from_bias_payload(bias_payload)

        conn_k = sqlite3.connect(str(kdb))
        conn_k.execute("PRAGMA journal_mode=WAL")
        _ensure_schema(conn_k)
        conn_k.execute(
            """
            INSERT OR REPLACE INTO race_scenarios (
                race_id, expected_pace, expected_bias, race_complexity, winning_pattern,
                front_collapse_probability, inside_advantage, outside_advantage,
                stalker_advantage, closer_advantage, recommended_styles_json,
                expected_front_count, pace_pressure_index, source_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                race_id,
                str(scen["expected_pace"]),
                str(scen["expected_bias"]),
                str(scen["race_complexity"]),
                str(scen["winning_pattern"]),
                float(scen["front_collapse_probability"]),
                float(scen["inside_advantage"]),
                float(scen["outside_advantage"]),
                float(scen["stalker_advantage"]),
                float(scen["closer_advantage"]),
                json.dumps(scen.get("recommended_styles") or [], ensure_ascii=False),
                int(scen["expected_front_count"]),
                float(scen["pace_pressure_index"]),
                json.dumps(scen.get("source") or {}, ensure_ascii=False, default=str),
                _now(),
            ),
        )
        conn_k.commit()

        out = {
            "race_id": race_id,
            **{k: v for k, v in scen.items() if k != "source"},
        }
        conn_k.close()
        return out

    if row is None:
        conn_k.close()
        return {"race_id": race_id, "message": "scenario_not_found"}

    try:
        rec = json.loads(row[10] or "[]")
    except Exception:
        rec = []

    out = {
        "race_id": str(row[0]),
        "expected_pace": str(row[1]),
        "expected_bias": str(row[2]),
        "race_complexity": str(row[3]),
        "winning_pattern": str(row[4]),
        "front_collapse_probability": float(row[5] or 0.0),
        "inside_advantage": float(row[6] or 0.0),
        "outside_advantage": float(row[7] or 0.0),
        "stalker_advantage": float(row[8] or 0.0),
        "closer_advantage": float(row[9] or 0.0),
        "recommended_styles": rec,
        "expected_front_count": int(row[11] or 0),
        "pace_pressure_index": float(row[12] or 0.0),
    }
    conn_k.close()
    return out


def scenario_feature_dict(s: dict[str, Any]) -> dict[str, Any]:
    pace = str(s.get("expected_pace") or "unknown")
    bias = str(s.get("expected_bias") or "neutral")
    complexity = str(s.get("race_complexity") or "low")
    top_style = ""
    rec = s.get("recommended_styles")
    if isinstance(rec, list) and rec:
        top_style = str(rec[0])

    return {
        "scn_pace_fast": 1.0 if pace == "fast" else 0.0,
        "scn_pace_moderate": 1.0 if pace == "moderate" else 0.0,
        "scn_pace_slow": 1.0 if pace == "slow" else 0.0,
        "scn_bias_inside": 1.0 if bias == "inside" else 0.0,
        "scn_bias_outside": 1.0 if bias == "outside" else 0.0,
        "scn_complexity_high": 1.0 if complexity == "high" else 0.0,
        "scn_expected_front_count": float(_safe_int(s.get("expected_front_count"), 0)),
        "scn_front_collapse_probability": float(_safe_float(s.get("front_collapse_probability"), 0.0)),
        "scn_inside_advantage": float(_safe_float(s.get("inside_advantage"), 0.0)),
        "scn_outside_advantage": float(_safe_float(s.get("outside_advantage"), 0.0)),
        "scn_stalker_advantage": float(_safe_float(s.get("stalker_advantage"), 0.0)),
        "scn_closer_advantage": float(_safe_float(s.get("closer_advantage"), 0.0)),
        "scn_recommended_style_front": 1.0 if top_style == "front" else 0.0,
        "scn_recommended_style_stalker": 1.0 if top_style == "stalker" else 0.0,
        "scn_recommended_style_closer": 1.0 if top_style == "closer" else 0.0,
    }


def build_scenario_graph(scenario: dict[str, Any]) -> dict[str, Any]:
    pace = str(scenario.get("expected_pace") or "unknown")
    bias = str(scenario.get("expected_bias") or "neutral")
    winning_pattern = str(scenario.get("winning_pattern") or "stalker")
    collapse = float(_safe_float(scenario.get("front_collapse_probability"), 0.0))
    complexity = str(scenario.get("race_complexity") or "low")

    transition = "front_collapse" if collapse >= 0.5 else "front_hold"
    if winning_pattern == "closer":
        expected_position = "late_charge"
    elif winning_pattern == "front":
        expected_position = "pace_control"
    else:
        expected_position = "mid_pack_strike"

    # Graph shape: Pace -> Bias -> Style -> Position -> Finish
    nodes = [
        {"id": f"pace:{pace}", "type": "pace", "label": pace, "weight": round(abs(collapse - 0.5) + 0.5, 4)},
        {"id": f"bias:{bias}", "type": "bias", "label": bias, "weight": round(1.0 if bias != "neutral" else 0.6, 4)},
        {"id": f"transition:{transition}", "type": "transition", "label": transition, "weight": round(0.5 + collapse, 4)},
        {"id": f"style:{winning_pattern}", "type": "style", "label": winning_pattern, "weight": round(0.7 + (0.2 if complexity == "high" else 0.0), 4)},
        {"id": f"position:{expected_position}", "type": "position", "label": expected_position, "weight": round(0.8 + (0.1 if complexity != "low" else 0.0), 4)},
        {"id": "finish:win", "type": "finish", "label": "win", "weight": 1.0},
    ]
    edges = [
        {"source": f"pace:{pace}", "target": f"transition:{transition}", "weight": round(0.55 + abs(collapse - 0.5), 4)},
        {"source": f"transition:{transition}", "target": f"style:{winning_pattern}", "weight": round(0.65 + collapse * 0.25, 4)},
        {"source": f"bias:{bias}", "target": f"style:{winning_pattern}", "weight": round(0.6 if bias != "neutral" else 0.35, 4)},
        {"source": f"style:{winning_pattern}", "target": f"position:{expected_position}", "weight": round(0.72 + abs(collapse - 0.5) * 0.2, 4)},
        {"source": f"position:{expected_position}", "target": "finish:win", "weight": round(0.7 + (0.15 if complexity == "high" else 0.05), 4)},
    ]
    graph_signature = {
        "pace": pace,
        "bias": bias,
        "transition": transition,
        "style": winning_pattern,
        "position": expected_position,
        "complexity": complexity,
    }
    graph_hash = hashlib.sha1(json.dumps(graph_signature, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    scenario_id = f"scn_{pace}_{bias}_{winning_pattern}_{complexity}"

    return {
        "scenario_id": scenario_id,
        "scenario_hash": graph_hash,
        "nodes": nodes,
        "edges": edges,
        "main_path": [
            f"pace:{pace}",
            f"transition:{transition}",
            f"style:{winning_pattern}",
            f"position:{expected_position}",
            "finish:win",
        ],
    }


def explain_prediction_reason(
    *,
    prediction: dict[str, Any],
    scenario: dict[str, Any],
    race_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pace = str(scenario.get("expected_pace") or "unknown")
    bias = str(scenario.get("expected_bias") or "neutral")
    pattern = str(scenario.get("winning_pattern") or "stalker")
    num_horses = _safe_int((race_info or {}).get("num_horses"), 0)

    horse_num = _safe_int(prediction.get("horse_number") or prediction.get("horse_no"), 0)
    odds = prediction.get("odds")
    pop = prediction.get("popularity")
    p_norm = _safe_float(prediction.get("p_norm"), 0.0)
    p_place3 = _safe_float(prediction.get("p_place3"), 0.0)
    p_ens = _safe_float(prediction.get("p_ensemble"), p_norm)

    reasons: list[str] = []
    reasons.append(f"{pace.capitalize()} Pace" if pace in {"fast", "moderate", "slow"} else "Pace Unknown")

    gate_fit = 0.5
    if num_horses > 0 and horse_num > 0:
        is_inside = horse_num <= max(1, num_horses // 2)
        if bias == "inside":
            gate_fit = 1.0 if is_inside else 0.2
            reasons.append("Inside Bias Fit" if is_inside else "Inside Bias Risk")
        elif bias == "outside":
            gate_fit = 1.0 if not is_inside else 0.2
            reasons.append("Outside Bias Fit" if not is_inside else "Outside Bias Risk")
        else:
            reasons.append("Neutral Bias")
    else:
        reasons.append("Gate Context Limited")

    style_fit = 0.5
    place_vs_win = p_place3 - p_norm
    if pattern == "closer":
        style_fit = 0.95 if place_vs_win >= 0.15 else 0.55
        reasons.append("Closer Fit" if style_fit >= 0.9 else "Closer Potential")
    elif pattern == "front":
        front_signal = (p_norm >= 0.12) or (_safe_float(pop, 99.0) <= 4.0)
        style_fit = 0.92 if front_signal else 0.52
        reasons.append("Front Control Fit" if front_signal else "Front Pressure Risk")
    else:
        style_fit = 0.9 if p_ens >= 0.1 else 0.55
        reasons.append("Stalker Balance Fit" if style_fit >= 0.85 else "Stalker Neutral")

    value_fit = 0.5
    if odds is not None:
        odds_f = _safe_float(odds, 0.0)
        if odds_f > 0:
            value_fit = min(1.0, max(0.1, p_ens * odds_f / 2.0))
            reasons.append("Value Match" if value_fit >= 0.6 else "Value Thin")
    elif pop is not None:
        value_fit = 0.75 if _safe_float(pop, 99.0) <= 5 else 0.45
        reasons.append("Jockey Match" if _safe_float(pop, 99.0) <= 5 else "Jockey Risk")

    scenario_fit = max(0.0, min(1.0, (gate_fit * 0.35) + (style_fit * 0.45) + (value_fit * 0.20)))
    confidence = max(0.0, min(1.0, (p_ens * 0.45) + (scenario_fit * 0.55)))

    return {
        "reasons": reasons,
        "reason": " / ".join(reasons[:4]),
        "confidence": round(confidence, 4),
        "scenario_fit": round(scenario_fit, 4),
        "winning_pattern": pattern,
        "pace": pace,
        "bias": bias,
    }


def attach_scenario_features_to_frame(
    *,
    df,
    race_db_path: str,
    knowledge_db_path: str | None = None,
    race_id_col: str = "race_id",
):
    # Keep pandas optional to avoid hard dependency during import time in lightweight paths.
    import pandas as pd  # type: ignore

    if race_id_col not in df.columns:
        return df, []

    out = df.copy()
    race_ids = [str(x) for x in out[race_id_col].astype(str).dropna().unique().tolist()]
    scenario_map: dict[str, dict[str, Any]] = {}
    for rid in race_ids:
        if not rid:
            continue
        scenario_map[rid] = get_race_scenario(
            race_db_path=race_db_path,
            race_id=rid,
            knowledge_db_path=knowledge_db_path,
            auto_rebuild_if_missing=True,
        )

    features = [
        "scn_pace_fast",
        "scn_pace_moderate",
        "scn_pace_slow",
        "scn_bias_inside",
        "scn_bias_outside",
        "scn_complexity_high",
        "scn_expected_front_count",
        "scn_front_collapse_probability",
        "scn_inside_advantage",
        "scn_outside_advantage",
        "scn_stalker_advantage",
        "scn_closer_advantage",
        "scn_recommended_style_front",
        "scn_recommended_style_stalker",
        "scn_recommended_style_closer",
    ]

    for c in features:
        out[c] = 0.0

    def _row_feat(rid: Any) -> dict[str, Any]:
        s = scenario_map.get(str(rid), {})
        return scenario_feature_dict(s)

    feat_list = [_row_feat(rid) for rid in out[race_id_col].astype(str).tolist()]
    feat_df = pd.DataFrame(feat_list, index=out.index)
    for c in features:
        if c in feat_df.columns:
            out[c] = pd.to_numeric(feat_df[c], errors="coerce").fillna(0.0)

    return out, features


def suggest_scenario_interaction_nodes(df, *, max_nodes: int = 24) -> list[dict[str, Any]]:
    def _first_col(candidates: list[str]) -> str | None:
        for c in candidates:
            if c in df.columns:
                return c
        return None

    nodes: list[dict[str, Any]] = []

    closing_src = _first_col(["last_3f_time", "speed_figure", "p_place3", "p_norm"])
    gate_src = _first_col(["bracket_number", "horse_number"])
    pedigree_src = _first_col(["sire_rank", "dam_sire_rank", "avg_distance", "prev_race_distance"])
    stamina_src = _first_col(["distance", "prev_race_distance", "horse_total_runs"])

    # Meaningful candidates only: Scenario x horse factors with explicit semantics.
    if "scn_front_collapse_probability" in df.columns and closing_src:
        nodes.append(
            {
                "name": "fd_pace_closing_match",
                "type": "scenario_semantic_match",
                "scenario_col": "scn_front_collapse_probability",
                "horse_col": closing_src,
                "mode": "lower_is_better",
            }
        )

    if "scn_inside_advantage" in df.columns and gate_src:
        nodes.append(
            {
                "name": "fd_inside_gate_match",
                "type": "scenario_semantic_match",
                "scenario_col": "scn_inside_advantage",
                "horse_col": gate_src,
                "mode": "inside_gate_match",
                "max_gate": float(_safe_int(df[gate_src].max(), 18)),
            }
        )

    if "scn_outside_advantage" in df.columns and gate_src:
        nodes.append(
            {
                "name": "fd_outside_gate_match",
                "type": "scenario_semantic_match",
                "scenario_col": "scn_outside_advantage",
                "horse_col": gate_src,
                "mode": "outside_gate_match",
                "max_gate": float(_safe_int(df[gate_src].max(), 18)),
            }
        )

    if "scn_pace_fast" in df.columns and stamina_src:
        nodes.append(
            {
                "name": "fd_stamina_match",
                "type": "scenario_semantic_match",
                "scenario_col": "scn_pace_fast",
                "horse_col": stamina_src,
                "mode": "higher_is_better",
            }
        )

    if "scn_front_collapse_probability" in df.columns and pedigree_src:
        nodes.append(
            {
                "name": "fd_pace_pedigree_match",
                "type": "scenario_semantic_match",
                "scenario_col": "scn_front_collapse_probability",
                "horse_col": pedigree_src,
                "mode": "higher_is_better",
            }
        )

    if "scn_closer_advantage" in df.columns and "odds" in df.columns:
        nodes.append(
            {
                "name": "fd_closer_value_match",
                "type": "scenario_semantic_match",
                "scenario_col": "scn_closer_advantage",
                "horse_col": "odds",
                "mode": "higher_is_better",
            }
        )

    if "scn_stalker_advantage" in df.columns and "popularity" in df.columns:
        nodes.append(
            {
                "name": "fd_stalker_stability_match",
                "type": "scenario_semantic_match",
                "scenario_col": "scn_stalker_advantage",
                "horse_col": "popularity",
                "mode": "lower_is_better",
            }
        )

    return nodes[: int(max_nodes)]
