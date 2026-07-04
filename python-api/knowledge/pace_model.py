from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_knowledge_db() -> Path:
    return _repo_root() / "keiba" / "data" / "knowledge.db"


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        if isinstance(v, str):
            s = v.strip()
            if s == "":
                return default
            if "-" in s:
                s = s.split("-")[0].strip()
            return int(float(s))
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


def _first_corner_pos(row: dict[str, Any]) -> int:
    c1 = _safe_int(row.get("corner_1"), 0)
    if c1 > 0:
        return c1
    cps = row.get("corner_positions")
    if isinstance(cps, str) and cps.strip():
        return _safe_int(cps, 0)
    cplist = row.get("corner_positions_list")
    if isinstance(cplist, list) and cplist:
        return _safe_int(cplist[0], 0)
    return 0


def _pace_style(corner1: int, n_horses: int) -> str:
    n = max(1, int(n_horses))
    c = max(1, int(corner1))
    if c <= 2:
        return "front"
    if c <= max(3, int(n * 0.4)):
        return "stalker"
    if c <= max(5, int(n * 0.7)):
        return "mid"
    return "closer"


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pace_horse_profiles (
            horse_id TEXT PRIMARY KEY,
            sample_count INTEGER NOT NULL,
            avg_corner1 REAL NOT NULL,
            avg_finish REAL NOT NULL,
            front_rate REAL NOT NULL,
            stalker_rate REAL NOT NULL,
            closer_rate REAL NOT NULL,
            avg_pace_score REAL NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pace_profile_meta (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            updated_at TEXT NOT NULL,
            race_count INTEGER NOT NULL,
            horse_count INTEGER NOT NULL
        )
        """
    )


def _now() -> str:
    from datetime import datetime

    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def rebuild_pace_profiles(
    *,
    race_db_path: str,
    knowledge_db_path: str | None = None,
    lookback_per_horse: int = 8,
) -> dict[str, Any]:
    kdb = Path(knowledge_db_path) if knowledge_db_path else _default_knowledge_db()
    conn_k = sqlite3.connect(str(kdb))
    conn_k.execute("PRAGMA journal_mode=WAL")
    _ensure_schema(conn_k)

    conn_r = sqlite3.connect(race_db_path)

    race_meta: dict[str, dict[str, Any]] = {}
    for race_id, data_json in conn_r.execute("SELECT race_id, data FROM races_ultimate"):
        try:
            d = json.loads(data_json or "{}")
        except Exception:
            d = {}
        race_meta[str(race_id)] = d

    per_horse: dict[str, list[dict[str, Any]]] = {}
    rows = conn_r.execute("SELECT race_id, data FROM race_results_ultimate ORDER BY race_id DESC").fetchall()

    for race_id, data_json in rows:
        try:
            d = json.loads(data_json or "{}")
        except Exception:
            continue
        hid = str(d.get("horse_id") or "").strip()
        if not hid:
            continue
        arr = per_horse.get(hid)
        if arr is None:
            arr = []
            per_horse[hid] = arr
        if len(arr) >= int(max(1, lookback_per_horse)):
            continue

        meta = race_meta.get(str(race_id), {})
        n_horses = _safe_int(meta.get("num_horses"), 18)
        c1 = _first_corner_pos(d)
        fin = _safe_int(d.get("finish") or d.get("finish_position"), 0)
        if c1 <= 0:
            continue
        pace_score = (float(n_horses + 1 - c1) / float(max(1, n_horses)))
        style = _pace_style(c1, n_horses)
        arr.append(
            {
                "corner1": c1,
                "finish": fin,
                "pace_score": pace_score,
                "style": style,
            }
        )

    conn_k.execute("DELETE FROM pace_horse_profiles")

    horse_count = 0
    for hid, arr in per_horse.items():
        if not arr:
            continue
        n = len(arr)
        horse_count += 1
        avg_corner1 = sum(int(x["corner1"]) for x in arr) / float(n)
        avg_finish = sum(int(x["finish"]) for x in arr if int(x["finish"]) > 0)
        finish_n = sum(1 for x in arr if int(x["finish"]) > 0)
        avg_finish = (avg_finish / float(finish_n)) if finish_n > 0 else 0.0
        front_n = sum(1 for x in arr if x.get("style") == "front")
        stalker_n = sum(1 for x in arr if x.get("style") == "stalker")
        closer_n = sum(1 for x in arr if x.get("style") == "closer")
        avg_pace = sum(float(x["pace_score"]) for x in arr) / float(n)

        conn_k.execute(
            """
            INSERT INTO pace_horse_profiles (
                horse_id, sample_count, avg_corner1, avg_finish,
                front_rate, stalker_rate, closer_rate, avg_pace_score, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                hid,
                int(n),
                float(avg_corner1),
                float(avg_finish),
                float(front_n) / float(n),
                float(stalker_n) / float(n),
                float(closer_n) / float(n),
                float(avg_pace),
                _now(),
            ),
        )

    conn_k.execute(
        """
        INSERT OR REPLACE INTO pace_profile_meta (id, updated_at, race_count, horse_count)
        VALUES (1, ?, ?, ?)
        """,
        (_now(), int(len(rows)), int(horse_count)),
    )

    conn_k.commit()
    conn_k.close()
    conn_r.close()

    return {
        "race_count": int(len(rows)),
        "horse_count": int(horse_count),
        "lookback_per_horse": int(max(1, lookback_per_horse)),
    }


def _load_profiles(conn_k: sqlite3.Connection, horse_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not horse_ids:
        return {}
    ph = ",".join(["?"] * len(horse_ids))
    rows = conn_k.execute(
        f"SELECT horse_id, sample_count, avg_corner1, avg_finish, front_rate, stalker_rate, closer_rate, avg_pace_score FROM pace_horse_profiles WHERE horse_id IN ({ph})",
        horse_ids,
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        out[str(r[0])] = {
            "sample_count": int(r[1] or 0),
            "avg_corner1": float(r[2] or 0.0),
            "avg_finish": float(r[3] or 0.0),
            "front_rate": float(r[4] or 0.0),
            "stalker_rate": float(r[5] or 0.0),
            "closer_rate": float(r[6] or 0.0),
            "avg_pace_score": float(r[7] or 0.0),
        }
    return out


def analyze_race_pace(
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
        row = conn_k.execute("SELECT COUNT(*) FROM pace_horse_profiles").fetchone()
        if not row or int(row[0] or 0) <= 0:
            conn_k.close()
            rebuild_pace_profiles(race_db_path=race_db_path, knowledge_db_path=str(kdb))
            conn_k = sqlite3.connect(str(kdb))
            conn_k.execute("PRAGMA journal_mode=WAL")

    conn_r = sqlite3.connect(race_db_path)

    race_meta_row = conn_r.execute("SELECT data FROM races_ultimate WHERE race_id = ?", (race_id,)).fetchone()
    race_meta = {}
    if race_meta_row and race_meta_row[0]:
        try:
            race_meta = json.loads(race_meta_row[0] or "{}")
        except Exception:
            race_meta = {}

    participants: list[dict[str, Any]] = []
    rrows = conn_r.execute("SELECT data FROM race_results_ultimate WHERE race_id = ?", (race_id,)).fetchall()
    if rrows:
        for rr in rrows:
            try:
                d = json.loads(rr[0] or "{}")
            except Exception:
                continue
            participants.append(
                {
                    "horse_id": str(d.get("horse_id") or ""),
                    "horse_number": _safe_int(d.get("horse_number"), 0),
                    "horse_name": str(d.get("horse_name") or ""),
                    "odds": _safe_float(d.get("odds"), 0.0),
                    "popularity": _safe_int(d.get("popularity"), 0),
                }
            )
    else:
        erows = conn_r.execute(
            "SELECT horse_id, horse_no, horse_name, odds, popularity FROM entries WHERE race_id = ?",
            (race_id,),
        ).fetchall()
        for e in erows:
            participants.append(
                {
                    "horse_id": str(e[0] or ""),
                    "horse_number": _safe_int(e[1], 0),
                    "horse_name": str(e[2] or ""),
                    "odds": _safe_float(e[3], 0.0),
                    "popularity": _safe_int(e[4], 0),
                }
            )

    horse_ids = [str(p.get("horse_id") or "") for p in participants if str(p.get("horse_id") or "")]
    profiles = _load_profiles(conn_k, horse_ids)

    rows_out: list[dict[str, Any]] = []
    pace_pressure = 0.0

    for p in participants:
        hid = str(p.get("horse_id") or "")
        prof = profiles.get(hid, {})
        front_rate = float(prof.get("front_rate") or 0.0)
        stalker_rate = float(prof.get("stalker_rate") or 0.0)
        closer_rate = float(prof.get("closer_rate") or 0.0)
        pace_pressure += front_rate

        rows_out.append(
            {
                **p,
                "sample_count": int(prof.get("sample_count") or 0),
                "avg_corner1": float(prof.get("avg_corner1") or 0.0),
                "avg_finish": float(prof.get("avg_finish") or 0.0),
                "front_rate": front_rate,
                "stalker_rate": stalker_rate,
                "closer_rate": closer_rate,
                "pace_score": float(prof.get("avg_pace_score") or 0.0),
            }
        )

    n = max(1, len(rows_out))
    pressure = pace_pressure / float(n)
    if pressure >= 0.42:
        expected_pace = "fast"
    elif pressure <= 0.24:
        expected_pace = "slow"
    else:
        expected_pace = "moderate"

    for r in rows_out:
        if expected_pace == "fast":
            fit = 0.6 * float(r.get("closer_rate") or 0.0) + 0.4 * float(r.get("stalker_rate") or 0.0)
        elif expected_pace == "slow":
            fit = 0.7 * float(r.get("front_rate") or 0.0) + 0.3 * float(r.get("stalker_rate") or 0.0)
        else:
            fit = 0.45 * float(r.get("front_rate") or 0.0) + 0.45 * float(r.get("stalker_rate") or 0.0) + 0.1 * float(r.get("closer_rate") or 0.0)
        r["pace_fit_score"] = float(fit)

    rows_out.sort(key=lambda x: float(x.get("pace_fit_score") or 0.0), reverse=True)

    conn_r.close()
    conn_k.close()

    return {
        "race_id": race_id,
        "race_meta": {
            "venue": race_meta.get("venue"),
            "distance": race_meta.get("distance"),
            "surface": race_meta.get("track_type") or race_meta.get("surface"),
            "field_condition": race_meta.get("field_condition"),
            "date": race_meta.get("date"),
            "num_horses": race_meta.get("num_horses") or len(rows_out),
        },
        "expected_pace": expected_pace,
        "pace_pressure_index": float(round(pressure, 4)),
        "front_runner_count_est": int(sum(1 for r in rows_out if float(r.get("front_rate") or 0.0) >= 0.45)),
        "horses": rows_out,
    }
