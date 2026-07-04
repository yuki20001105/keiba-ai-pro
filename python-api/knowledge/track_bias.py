from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from knowledge.pace_model import analyze_race_pace  # type: ignore


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_knowledge_db() -> Path:
    return _repo_root() / "keiba" / "data" / "knowledge.db"


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        s = str(v).strip()
        if s == "":
            return default
        if "-" in s:
            s = s.split("-")[0].strip()
        return int(float(s))
    except Exception:
        return default


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _now() -> str:
    from datetime import datetime

    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _time_bucket(post_time: Any) -> str:
    t = str(post_time or "")
    if len(t) < 4:
        return "unknown"
    hh = _safe_int(t.split(":")[0], -1)
    if hh < 0:
        return "unknown"
    if hh <= 11:
        return "morning"
    if hh <= 14:
        return "afternoon"
    return "late"


def _pace_bucket(participants: list[dict[str, Any]], n_horses: int) -> str:
    if not participants:
        return "unknown"
    n = max(1, n_horses)
    front = 0
    known = 0
    for p in participants:
        c1 = _safe_int(p.get("corner_1"), 0)
        if c1 <= 0:
            c1 = _safe_int(p.get("corner_positions"), 0)
        if c1 > 0:
            known += 1
            if c1 <= 2:
                front += 1
    if known <= 0:
        return "unknown"
    r = float(front) / float(max(1, known))
    if r >= 0.30:
        return "fast"
    if r <= 0.16:
        return "slow"
    return "moderate"


def _style_of(row: dict[str, Any], n_horses: int) -> str:
    c1 = _safe_int(row.get("corner_1"), 0)
    if c1 <= 0:
        c1 = _safe_int(row.get("corner_positions"), 0)
    n = max(1, int(n_horses))
    if c1 <= 0:
        return "unknown"
    if c1 <= 2:
        return "front"
    if c1 <= max(3, int(n * 0.4)):
        return "stalker"
    if c1 <= max(5, int(n * 0.7)):
        return "mid"
    return "closer"


def _inside_outside_group(bracket: int, horse_number: int, n_horses: int) -> str:
    if bracket > 0:
        if bracket <= 3:
            return "inside"
        if bracket >= max(6, int((n_horses + 1) / 2)):
            return "outside"
        return "middle"
    if horse_number <= 0:
        return "middle"
    q1 = max(2, int(n_horses * 0.33))
    q2 = max(q1 + 1, int(n_horses * 0.66))
    if horse_number <= q1:
        return "inside"
    if horse_number >= q2:
        return "outside"
    return "middle"


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS track_bias_profiles (
            course TEXT NOT NULL,
            surface TEXT NOT NULL,
            distance INTEGER NOT NULL,
            condition TEXT NOT NULL,
            day INTEGER NOT NULL,
            pace_bucket TEXT NOT NULL,
            time_bucket TEXT NOT NULL,
            race_class TEXT NOT NULL,
            bias_inside REAL NOT NULL,
            bias_outside REAL NOT NULL,
            front_bias REAL NOT NULL,
            stalker_bias REAL NOT NULL,
            closer_bias REAL NOT NULL,
            pace_bias REAL NOT NULL,
            speed_bias REAL NOT NULL,
            sample_size INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (course, surface, distance, condition, day, pace_bucket, time_bucket, race_class)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS track_bias_meta (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            updated_at TEXT NOT NULL,
            race_count INTEGER NOT NULL,
            profile_count INTEGER NOT NULL
        )
        """
    )


def rebuild_track_bias_profiles(
    *,
    race_db_path: str,
    knowledge_db_path: str | None = None,
    min_races_per_profile: int = 12,
    max_races: int = 0,
) -> dict[str, Any]:
    kdb = Path(knowledge_db_path) if knowledge_db_path else _default_knowledge_db()
    conn_k = sqlite3.connect(str(kdb))
    conn_k.execute("PRAGMA journal_mode=WAL")
    _ensure_schema(conn_k)

    conn_r = sqlite3.connect(race_db_path)

    race_meta: dict[str, dict[str, Any]] = {}
    for race_id, data_json in conn_r.execute("SELECT race_id, data FROM races_ultimate ORDER BY race_id DESC"):
        try:
            d = json.loads(data_json or "{}")
        except Exception:
            d = {}
        race_meta[str(race_id)] = d

    agg: dict[tuple[Any, ...], dict[str, float]] = {}
    race_rows = conn_r.execute("SELECT race_id, data FROM race_results_ultimate ORDER BY race_id DESC").fetchall()

    if max_races > 0:
        allowed = set(str(r[0]) for r in list(race_meta.items())[: int(max_races)])
    else:
        allowed = None

    per_race: dict[str, list[dict[str, Any]]] = {}
    for race_id, data_json in race_rows:
        rid = str(race_id)
        if allowed is not None and rid not in allowed:
            continue
        try:
            d = json.loads(data_json or "{}")
        except Exception:
            continue
        arr = per_race.get(rid)
        if arr is None:
            arr = []
            per_race[rid] = arr
        arr.append(d)

    for rid, participants in per_race.items():
        meta = race_meta.get(rid, {})
        course = str(meta.get("venue") or "unknown")
        surface = str(meta.get("track_type") or meta.get("surface") or "unknown")
        distance = _safe_int(meta.get("distance"), 0)
        condition = str(meta.get("field_condition") or "unknown")
        day = _safe_int(meta.get("day"), 0)
        time_bucket = _time_bucket(meta.get("post_time"))
        race_class = str(meta.get("race_class") or "unknown")
        n_horses = _safe_int(meta.get("num_horses"), len(participants) if participants else 0)
        pace_bucket = _pace_bucket(participants, n_horses)

        if distance <= 0 or course == "unknown" or surface == "unknown":
            continue

        k = (course, surface, distance, condition, day, pace_bucket, time_bucket, race_class)
        m = agg.get(k)
        if m is None:
            m = {
                "races": 0.0,
                "inside_lift": 0.0,
                "outside_lift": 0.0,
                "front_lift": 0.0,
                "stalker_lift": 0.0,
                "closer_lift": 0.0,
                "pace_val": 0.0,
                "speed_val": 0.0,
            }
            agg[k] = m

        m["races"] += 1.0

        winners = []
        top3 = []
        inside_n = 0
        outside_n = 0
        front_n = 0
        stalker_n = 0
        closer_n = 0
        known_style_n = 0

        for p in participants:
            fin = _safe_int(p.get("finish") or p.get("finish_position"), 0)
            if fin <= 0:
                continue

            bracket = _safe_int(p.get("bracket_number"), 0)
            hno = _safe_int(p.get("horse_number"), 0)
            io = _inside_outside_group(bracket, hno, max(1, n_horses))
            style = _style_of(p, max(1, n_horses))

            if io == "inside":
                inside_n += 1
            elif io == "outside":
                outside_n += 1

            if style == "front":
                front_n += 1
                known_style_n += 1
            elif style == "stalker":
                stalker_n += 1
                known_style_n += 1
            elif style == "closer":
                closer_n += 1
                known_style_n += 1
            elif style == "mid":
                known_style_n += 1

            if fin == 1:
                winners.append({"io": io, "style": style, "last3f_rank": _safe_int(p.get("last_3f_rank"), 0)})
            if fin <= 3:
                top3.append({"io": io, "style": style, "last3f_rank": _safe_int(p.get("last_3f_rank"), 0)})

        if not winners or n_horses <= 0:
            continue

        top3_n = max(1, len(top3))
        inside_share = float(inside_n) / float(max(1, n_horses))
        outside_share = float(outside_n) / float(max(1, n_horses))

        inside_top3 = sum(1 for x in top3 if x["io"] == "inside") / float(top3_n)
        outside_top3 = sum(1 for x in top3 if x["io"] == "outside") / float(top3_n)
        m["inside_lift"] += (inside_top3 - inside_share)
        m["outside_lift"] += (outside_top3 - outside_share)

        known = max(1, known_style_n)
        front_share = float(front_n) / float(known)
        stalker_share = float(stalker_n) / float(known)
        closer_share = float(closer_n) / float(known)

        front_top3 = sum(1 for x in top3 if x["style"] == "front") / float(top3_n)
        stalker_top3 = sum(1 for x in top3 if x["style"] == "stalker") / float(top3_n)
        closer_top3 = sum(1 for x in top3 if x["style"] == "closer") / float(top3_n)

        m["front_lift"] += (front_top3 - front_share)
        m["stalker_lift"] += (stalker_top3 - stalker_share)
        m["closer_lift"] += (closer_top3 - closer_share)

        m["pace_val"] += (1.0 if pace_bucket == "fast" else (-1.0 if pace_bucket == "slow" else 0.0))

        winner_l3 = [x["last3f_rank"] for x in winners if x.get("last3f_rank", 0) > 0]
        if winner_l3:
            avg_l3 = sum(winner_l3) / float(len(winner_l3))
            speed_val = 1.0 / float(max(1.0, avg_l3))
            m["speed_val"] += speed_val

    conn_k.execute("DELETE FROM track_bias_profiles")

    profile_count = 0
    for k, m in agg.items():
        races = int(m.get("races") or 0)
        if races < max(3, int(min_races_per_profile)):
            continue
        profile_count += 1
        v = lambda x: float(m.get(x, 0.0)) / float(max(1, races))
        conn_k.execute(
            """
            INSERT OR REPLACE INTO track_bias_profiles (
                course, surface, distance, condition, day, pace_bucket, time_bucket, race_class,
                bias_inside, bias_outside, front_bias, stalker_bias, closer_bias,
                pace_bias, speed_bias, sample_size, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(k[0]),
                str(k[1]),
                int(k[2]),
                str(k[3]),
                int(k[4]),
                str(k[5]),
                str(k[6]),
                str(k[7]),
                v("inside_lift"),
                v("outside_lift"),
                v("front_lift"),
                v("stalker_lift"),
                v("closer_lift"),
                v("pace_val"),
                v("speed_val"),
                int(races),
                _now(),
            ),
        )

    conn_k.execute(
        """
        INSERT OR REPLACE INTO track_bias_meta (id, updated_at, race_count, profile_count)
        VALUES (1, ?, ?, ?)
        """,
        (_now(), int(len(per_race)), int(profile_count)),
    )

    conn_k.commit()
    conn_k.close()
    conn_r.close()

    return {
        "race_count": int(len(per_race)),
        "profile_count": int(profile_count),
        "min_races_per_profile": int(max(3, int(min_races_per_profile))),
    }


def _load_best_profile(
    *,
    conn_k: sqlite3.Connection,
    meta: dict[str, Any],
    expected_pace: str,
) -> dict[str, Any] | None:
    course = str(meta.get("venue") or "unknown")
    surface = str(meta.get("track_type") or meta.get("surface") or "unknown")
    distance = _safe_int(meta.get("distance"), 0)
    condition = str(meta.get("field_condition") or "unknown")
    day = _safe_int(meta.get("day"), 0)
    race_class = str(meta.get("race_class") or "unknown")
    t_bucket = _time_bucket(meta.get("post_time"))

    rows = conn_k.execute(
        """
        SELECT course, surface, distance, condition, day, pace_bucket, time_bucket, race_class,
               bias_inside, bias_outside, front_bias, stalker_bias, closer_bias,
               pace_bias, speed_bias, sample_size, updated_at
        FROM track_bias_profiles
        WHERE course = ? AND surface = ?
          AND distance BETWEEN ? AND ?
        ORDER BY sample_size DESC
        LIMIT 500
        """,
        (course, surface, max(0, distance - 200), distance + 200),
    ).fetchall()

    if not rows:
        return None

    best = None
    best_score = -999999
    for r in rows:
        score = 0
        if str(r[3]) == condition:
            score += 4
        if int(r[4] or 0) == day and day > 0:
            score += 3
        if str(r[5]) == expected_pace:
            score += 2
        if str(r[6]) == t_bucket:
            score += 1
        if str(r[7]) == race_class:
            score += 1

        dist = abs(int(r[2] or 0) - distance)
        score -= min(4, int(dist / 100))

        score2 = score * 100000 + int(r[15] or 0)
        if score2 > best_score:
            best_score = score2
            best = r

    if best is None:
        return None

    return {
        "course": str(best[0]),
        "surface": str(best[1]),
        "distance": int(best[2] or 0),
        "condition": str(best[3]),
        "day": int(best[4] or 0),
        "pace_bucket": str(best[5]),
        "time_bucket": str(best[6]),
        "race_class": str(best[7]),
        "bias_inside": float(best[8] or 0.0),
        "bias_outside": float(best[9] or 0.0),
        "front_bias": float(best[10] or 0.0),
        "stalker_bias": float(best[11] or 0.0),
        "closer_bias": float(best[12] or 0.0),
        "pace_bias": float(best[13] or 0.0),
        "speed_bias": float(best[14] or 0.0),
        "sample_size": int(best[15] or 0),
        "updated_at": str(best[16] or ""),
    }


def analyze_track_bias(
    *,
    race_db_path: str,
    race_id: str,
    knowledge_db_path: str | None = None,
    auto_rebuild_if_empty: bool = True,
) -> dict[str, Any]:
    kdb = Path(knowledge_db_path) if knowledge_db_path else _default_knowledge_db()

    conn_k = sqlite3.connect(str(kdb))
    conn_k.execute("PRAGMA journal_mode=WAL")
    _ensure_schema(conn_k)

    if auto_rebuild_if_empty:
        c = conn_k.execute("SELECT COUNT(*) FROM track_bias_profiles").fetchone()
        if not c or int(c[0] or 0) <= 0:
            conn_k.close()
            rebuild_track_bias_profiles(race_db_path=race_db_path, knowledge_db_path=str(kdb))
            conn_k = sqlite3.connect(str(kdb))
            conn_k.execute("PRAGMA journal_mode=WAL")

    conn_r = sqlite3.connect(race_db_path)
    meta_row = conn_r.execute("SELECT data FROM races_ultimate WHERE race_id = ?", (race_id,)).fetchone()
    conn_r.close()

    if not meta_row:
        conn_k.close()
        return {"race_id": race_id, "message": "race_not_found"}

    try:
        meta = json.loads(meta_row[0] or "{}")
    except Exception:
        meta = {}

    pace = analyze_race_pace(
        race_db_path=race_db_path,
        race_id=race_id,
        knowledge_db_path=str(kdb),
        auto_rebuild_if_empty=True,
    )
    expected_pace = str(pace.get("expected_pace") or "unknown")

    prof = _load_best_profile(conn_k=conn_k, meta=meta, expected_pace=expected_pace)
    conn_k.close()

    if not prof:
        return {
            "race_id": race_id,
            "expected_bias": "neutral",
            "inside_bias": 0.0,
            "outside_bias": 0.0,
            "front_bias": 0.0,
            "stalker_bias": 0.0,
            "closer_bias": 0.0,
            "pace_bias": 0.0,
            "speed_bias": 0.0,
            "recommended_running_style": "stalker",
            "expected_pace": expected_pace,
            "message": "no_bias_profile_matched",
        }

    inside = float(prof.get("bias_inside") or 0.0)
    outside = float(prof.get("bias_outside") or 0.0)
    if outside - inside > 0.02:
        expected_bias = "outside"
    elif inside - outside > 0.02:
        expected_bias = "inside"
    else:
        expected_bias = "neutral"

    style_scores = {
        "front": float(prof.get("front_bias") or 0.0),
        "stalker": float(prof.get("stalker_bias") or 0.0),
        "closer": float(prof.get("closer_bias") or 0.0),
    }
    recommended_running_style = sorted(style_scores.items(), key=lambda x: x[1], reverse=True)[0][0]

    scenario = f"pace={expected_pace}, bias={expected_bias}, style={recommended_running_style}"

    return {
        "race_id": race_id,
        "expected_bias": expected_bias,
        "inside_bias": inside,
        "outside_bias": outside,
        "front_bias": float(prof.get("front_bias") or 0.0),
        "stalker_bias": float(prof.get("stalker_bias") or 0.0),
        "closer_bias": float(prof.get("closer_bias") or 0.0),
        "pace_bias": float(prof.get("pace_bias") or 0.0),
        "speed_bias": float(prof.get("speed_bias") or 0.0),
        "recommended_running_style": recommended_running_style,
        "expected_pace": expected_pace,
        "pace_pressure_index": float(pace.get("pace_pressure_index") or 0.0),
        "front_runner_count_est": int(pace.get("front_runner_count_est") or 0),
        "race_scenario": scenario,
        "matched_profile": prof,
    }
