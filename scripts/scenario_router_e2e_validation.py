from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]
MLOPS_DB = Path(
    os.environ.get("SCENARIO_ROUTER_AUDIT_DB_PATH")
    or (ROOT / "keiba" / "data" / "mlops.db")
)
RACE_DB = Path(
    os.environ.get("SCENARIO_ROUTER_AUDIT_RACE_DB_PATH")
    or (ROOT / "keiba" / "data" / "keiba_ultimate.db")
)
BASE_URL = os.environ.get("API_BASE_URL", "http://127.0.0.1:8000")
REQUEST_MODEL_ID = os.environ.get(
    "E2E_MODEL_ID",
    "model_speed_deviation_lightgbm_20130101_20180128_20260612_2207",
)


@dataclass
class Case:
    name: str
    router_mode: str
    canary_percent: int | None
    expected_effective: str


def _candidate_race_ids(limit: int = 30) -> list[str]:
    conn = sqlite3.connect(str(RACE_DB))
    out: list[str] = []
    try:
        # 直近で analyze_race 成功実績がある race_id を優先
        try:
            rows = conn.execute(
                """
                SELECT race_id
                FROM prediction_log
                GROUP BY race_id
                HAVING COUNT(*) >= 8
                ORDER BY MAX(id) DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
            if rows:
                out.extend([str(r[0]) for r in rows if r and r[0]])
        except Exception:
            pass

        rows2 = conn.execute(
            """
            SELECT race_id
            FROM race_results_ultimate
            GROUP BY race_id
            HAVING COUNT(*) >= 8
            ORDER BY race_id DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        out.extend([str(r[0]) for r in rows2 if r and r[0]])
    finally:
        conn.close()

    dedup: list[str] = []
    seen: set[str] = set()
    for rid in out:
        if rid in seen:
            continue
        seen.add(rid)
        dedup.append(rid)
    if not dedup:
        raise RuntimeError("E2E用のrace候補を取得できませんでした")
    return dedup


def _existing_prediction_ids_for_race(race_id: str) -> set[str]:
    conn = sqlite3.connect(str(MLOPS_DB))
    try:
        rows = conn.execute(
            "SELECT prediction_id FROM prediction_runs WHERE race_id = ?",
            (race_id,),
        ).fetchall()
    finally:
        conn.close()
    return {str(r[0]) for r in rows if r and r[0]}


def _wait_new_prediction(
    race_id: str,
    before_ids: set[str],
    expected_router_mode: str,
    expected_effective: str,
    expected_canary_percent: int | None,
    timeout_sec: float = 60.0,
) -> dict:
    deadline = time.time() + timeout_sec
    last_row: dict | None = None
    while time.time() < deadline:
        conn = sqlite3.connect(str(MLOPS_DB))
        try:
            rows = conn.execute(
                """
                SELECT prediction_id, race_id, model_id,
                       selected_model_id, actual_model_id,
                       shadow_selected_model_id,
                       router_mode, effective_router_mode,
                       canary_percent, canary_bucket, canary_selected
                FROM prediction_runs
                WHERE race_id = ?
                ORDER BY created_at DESC
                LIMIT 20
                """,
                (race_id,),
            ).fetchall()
        finally:
            conn.close()

        for r in rows:
            pid = str(r[0] or "")
            if not pid or pid in before_ids:
                continue
            row = {
                "prediction_id": pid,
                "race_id": str(r[1] or ""),
                "model_id": str(r[2] or ""),
                "selected_model_id": str(r[3] or ""),
                "actual_model_id": str(r[4] or ""),
                "shadow_selected_model_id": str(r[5] or ""),
                "router_mode": str(r[6] or ""),
                "effective_router_mode": str(r[7] or ""),
                "canary_percent": (int(r[8]) if r[8] is not None else None),
                "canary_bucket": (int(r[9]) if r[9] is not None else None),
                "canary_selected": bool(int(r[10] or 0)),
            }
            last_row = row
            if row["router_mode"] != expected_router_mode:
                continue
            if row["effective_router_mode"] != expected_effective:
                continue
            if expected_canary_percent is not None and row["canary_percent"] != expected_canary_percent:
                continue
            return row

        time.sleep(0.4)

    if last_row is not None:
        raise RuntimeError(f"prediction_run検出はできたが期待条件不一致: {last_row}")
    raise RuntimeError("prediction_run がタイムアウト内に保存されませんでした")


def _count_prediction_results(prediction_id: str) -> int:
    conn = sqlite3.connect(str(MLOPS_DB))
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM prediction_results WHERE prediction_id = ?",
            (prediction_id,),
        ).fetchone()
        return int((row or [0])[0])
    finally:
        conn.close()


def _insert_legacy_prediction_row(race_id: str) -> str:
    legacy_id = f"pred_legacy_e2e_{uuid.uuid4().hex[:10]}"
    conn = sqlite3.connect(str(MLOPS_DB))
    try:
        conn.execute(
            """
            INSERT INTO prediction_runs (
                prediction_id, created_at, race_id, race_date, model_id,
                quality_gate_json, metadata_json
            ) VALUES (?, datetime('now'), ?, ?, ?, '{}', '{}')
            """,
            (legacy_id, race_id, race_id[:8], "legacy_model"),
        )
        conn.execute(
            """
            INSERT INTO prediction_results (
                prediction_id, horse_id, horse_number, horse_name,
                score, probability, calibrated_probability, rank,
                expected_value, odds, buy_flag, reason, confidence, scenario_fit
            ) VALUES (?, ?, 1, ?, 0.5, 0.5, 0.5, 1, 1.0, 2.0, 0, '', NULL, NULL)
            """,
            (legacy_id, f"legacy_h_{uuid.uuid4().hex[:6]}", "legacy_horse"),
        )
        conn.commit()
    finally:
        conn.close()
    return legacy_id


def _delete_predictions(prediction_ids: list[str]) -> None:
    ids = [str(x) for x in prediction_ids if str(x)]
    if not ids:
        return
    ph = ",".join(["?"] * len(ids))
    conn = sqlite3.connect(str(MLOPS_DB), timeout=20)
    try:
        conn.execute(f"DELETE FROM prediction_results WHERE prediction_id IN ({ph})", ids)
        conn.execute(f"DELETE FROM prediction_runs WHERE prediction_id IN ({ph})", ids)
        conn.commit()
    finally:
        conn.close()


def main() -> int:
    if not MLOPS_DB.exists() or not RACE_DB.exists():
        print("db_exists", False)
        return 1

    race_candidates = _candidate_race_ids()
    created_ids: list[str] = []

    cases = [
        Case("off", "off", None, "off"),
        Case("shadow", "shadow", None, "shadow"),
        Case("canary0", "canary", 0, "shadow"),
        Case("canary100", "canary", 100, "active"),
        Case("active", "active", None, "active"),
    ]

    print("base_url", BASE_URL)
    print("race_candidates", len(race_candidates))
    print("request_model_id", REQUEST_MODEL_ID)

    canary_bucket_seen: dict[int, int] = {}
    mode_rows: dict[str, dict] = {}
    race_id = ""

    try:
        with httpx.Client(timeout=300.0) as client:
            health = client.get(f"{BASE_URL}/health")
            print("health_ok", health.status_code == 200)
            if health.status_code != 200:
                print("health_status", health.status_code)
                return 1

            # 最初に off が通る race_id を選定
            first = cases[0]
            for rid in race_candidates:
                before_ids = _existing_prediction_ids_for_race(rid)
                payload = {
                    "race_id": rid,
                    "model_id": REQUEST_MODEL_ID,
                    "bankroll": 10000,
                    "risk_mode": "balanced",
                    "router_mode": first.router_mode,
                    "use_scenario_router": False,
                }
                resp = client.post(f"{BASE_URL}/api/analyze_race", json=payload)
                print(f"off_probe_{rid}", resp.status_code == 200)
                if resp.status_code != 200:
                    continue
                race_id = rid
                row = _wait_new_prediction(
                    race_id=rid,
                    before_ids=before_ids,
                    expected_router_mode=first.router_mode,
                    expected_effective=first.expected_effective,
                    expected_canary_percent=first.canary_percent,
                )
                created_ids.append(row["prediction_id"])
                mode_rows[first.name] = row
                print("race_id", race_id)
                print("off_api_200", True)
                print("off_prediction_runs_saved", bool(row["prediction_id"]))
                print("off_prediction_results_saved", _count_prediction_results(row["prediction_id"]) > 0)
                print("off_router_mode_saved", row["router_mode"] == first.router_mode)
                print("off_effective_router_mode_saved", row["effective_router_mode"] == first.expected_effective)
                print("off_actual_model_id_correct", row["actual_model_id"] == REQUEST_MODEL_ID)
                print("off_selected_model_id_saved", bool(row["selected_model_id"]))
                print("off_shadow_selected_model_id_saved", row["shadow_selected_model_id"] is not None)
                break

            if not race_id:
                print("off_api_200", False)
                print("off_status", "all_candidates_failed")
                return 1

            for c in cases[1:]:
                before_ids = _existing_prediction_ids_for_race(race_id)
                payload: dict[str, object] = {
                    "race_id": race_id,
                    "model_id": REQUEST_MODEL_ID,
                    "bankroll": 10000,
                    "risk_mode": "balanced",
                    "router_mode": c.router_mode,
                    "use_scenario_router": False,
                }
                if c.canary_percent is not None:
                    payload["canary_percent"] = int(c.canary_percent)

                resp = client.post(f"{BASE_URL}/api/analyze_race", json=payload)
                print(f"{c.name}_api_200", resp.status_code == 200)
                if resp.status_code != 200:
                    print(f"{c.name}_status", resp.status_code)
                    print(f"{c.name}_body", resp.text[:500])
                    return 1

                row = _wait_new_prediction(
                    race_id=race_id,
                    before_ids=before_ids,
                    expected_router_mode=c.router_mode,
                    expected_effective=c.expected_effective,
                    expected_canary_percent=c.canary_percent,
                )
                created_ids.append(row["prediction_id"])
                mode_rows[c.name] = row

                pr_saved = bool(row["prediction_id"])
                pres_saved = _count_prediction_results(row["prediction_id"]) > 0
                eff_ok = row["effective_router_mode"] == c.expected_effective
                route_ok = row["router_mode"] == c.router_mode
                actual_ok = row["actual_model_id"] == REQUEST_MODEL_ID
                selected_present = bool(row["selected_model_id"])
                shadow_selected_present = row["shadow_selected_model_id"] is not None

                print(f"{c.name}_prediction_runs_saved", pr_saved)
                print(f"{c.name}_prediction_results_saved", pres_saved)
                print(f"{c.name}_router_mode_saved", route_ok)
                print(f"{c.name}_effective_router_mode_saved", eff_ok)
                print(f"{c.name}_actual_model_id_correct", actual_ok)
                print(f"{c.name}_selected_model_id_saved", selected_present)
                print(f"{c.name}_shadow_selected_model_id_saved", shadow_selected_present)

                if c.router_mode == "shadow":
                    off_actual = str((mode_rows.get("off") or {}).get("actual_model_id") or "")
                    print("shadow_actual_not_switched", bool(off_actual) and row["actual_model_id"] == off_actual)
                if c.router_mode == "active":
                    print("active_selected_model_id_non_empty", selected_present)

                if c.router_mode == "canary":
                    cp = int(c.canary_percent or 0)
                    print(f"{c.name}_canary_percent_saved", row["canary_percent"] == cp)
                    print(f"{c.name}_canary_bucket_saved", row["canary_bucket"] is not None)
                    print(f"{c.name}_canary_selected_saved", isinstance(row["canary_selected"], bool))
                    if row["canary_bucket"] is not None:
                        if cp in canary_bucket_seen:
                            print(f"{c.name}_canary_bucket_deterministic", canary_bucket_seen[cp] == int(row["canary_bucket"]))
                        else:
                            canary_bucket_seen[cp] = int(row["canary_bucket"])

            # Canary 0 / 100 は同じ race_id で bucket が一致するべき
            if 0 in canary_bucket_seen and 100 in canary_bucket_seen:
                print("canary_bucket_same_for_same_race", canary_bucket_seen[0] == canary_bucket_seen[100])

            legacy_id = _insert_legacy_prediction_row(race_id)
            created_ids.append(legacy_id)

            list_resp = client.get(f"{BASE_URL}/api/mlops/predictions", params={"limit": 50})
            print("list_api_200", list_resp.status_code == 200)
            if list_resp.status_code == 200:
                items = (list_resp.json() or {}).get("items") or []
                has_legacy = any(str(x.get("prediction_id") or "") == legacy_id for x in items if isinstance(x, dict))
                has_new = any(str(x.get("prediction_id") or "") in set(created_ids) for x in items if isinstance(x, dict))
                print("list_api_contains_legacy", has_legacy)
                print("list_api_contains_new", has_new)
            else:
                print("list_api_status", list_resp.status_code)

            get_resp = client.get(f"{BASE_URL}/api/mlops/predictions/{legacy_id}")
            print("get_api_200", get_resp.status_code == 200)
            if get_resp.status_code == 200:
                data = get_resp.json() or {}
                print("get_api_legacy_id_match", str(data.get("prediction_id") or "") == legacy_id)
                print("get_api_legacy_results_present", isinstance(data.get("results"), list))
            else:
                print("get_api_status", get_resp.status_code)

        return 0
    finally:
        try:
            _delete_predictions(created_ids)
            print("cleanup_done", True)
        except Exception:
            print("cleanup_done", False)


if __name__ == "__main__":
    sys.exit(main())
