from __future__ import annotations

import base64
import json
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[1]
MLOPS_DB = Path(
    os.environ.get("SCENARIO_ROUTER_AUDIT_DB_PATH")
    or (ROOT / "keiba" / "data" / "mlops.db")
)
BASE_URL = os.environ.get("API_BASE_URL", "http://127.0.0.1:8000")
TIMEOUT_SEC = float(os.environ.get("AUTO_RECOVERY_E2E_TIMEOUT_SEC", "120"))


def _ts() -> str:
    return time.strftime("%Y%m%d%H%M%S", time.localtime())


def _json_b64(data: dict[str, Any]) -> str:
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _fake_admin_token() -> str:
    # Local fallback mode can read unverified claims when JWKS is unavailable.
    header = _json_b64({"alg": "HS256", "typ": "JWT"})
    payload = _json_b64(
        {
            "sub": "local-dev-e2e",
            "role": "admin",
            "app_metadata": {"role": "admin"},
            "user_metadata": {"role": "admin", "subscription_tier": "premium"},
            "exp": int(time.time()) + 3600,
        }
    )
    return f"{header}.{payload}.sig"


def _auth_headers() -> dict[str, str]:
    tok = str(os.environ.get("E2E_BEARER_TOKEN") or "").strip()
    if not tok and str(os.environ.get("E2E_USE_FAKE_ADMIN_TOKEN") or "").strip() in {"1", "true", "TRUE"}:
        tok = _fake_admin_token()
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def _print_check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    if detail:
        print(f"[{status}] {name}: {detail}")
    else:
        print(f"[{status}] {name}")


def _post(client: httpx.Client, path: str, body: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
    return client.post(f"{BASE_URL}{path}", json=body, headers=headers)


def _get(client: httpx.Client, path: str, params: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
    return client.get(f"{BASE_URL}{path}", params=params, headers=headers)


def _create_temp_alert() -> str:
    aid = f"sra_e2e_auto_{uuid.uuid4().hex[:10]}"
    now = _ts()
    conn = sqlite3.connect(str(MLOPS_DB), timeout=20)
    try:
        conn.execute(
            """
            INSERT INTO scenario_router_alerts (
                alert_id, target, severity, alert_type,
                status, title, message,
                source_run_id, decision, action,
                summary_json, created_at, resolved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                aid,
                "win",
                "WARNING",
                "RUN_FAILED",
                "open",
                "auto recovery e2e",
                "temporary alert for HTTP e2e smoke",
                f"e2e_auto_recovery_{uuid.uuid4().hex[:8]}",
                "STOP_CANARY",
                "STOP",
                json.dumps(
                    {
                        "run_failed": True,
                        "no_model_rate": 0.42,
                        "fallback_rate": 0.31,
                        "roi_lift": -0.18,
                        "hit_rate_lift": -0.09,
                        "thresholds": {
                            "max_no_model_rate": 0.2,
                            "max_fallback_rate": 0.15,
                            "min_roi_lift": 0.0,
                            "min_hit_rate_lift": 0.0,
                        },
                    },
                    ensure_ascii=False,
                ),
                now,
            ),
        )
        conn.commit()
        return aid
    finally:
        conn.close()


def _snapshot_state(alert_id: str) -> dict[str, Any]:
    conn = sqlite3.connect(str(MLOPS_DB), timeout=20)
    try:
        a = conn.execute(
            "SELECT status, resolved_at FROM scenario_router_alerts WHERE alert_id = ? LIMIT 1",
            (alert_id,),
        ).fetchone()
        p = conn.execute("SELECT COUNT(*) FROM scenario_router_auto_recovery_policies").fetchone()
        r = conn.execute(
            "SELECT status, current_percent, router_mode FROM scenario_router_rollouts WHERE target = 'win' LIMIT 1"
        ).fetchone()
        return {
            "alert_status": str((a or [""])[0] or ""),
            "alert_resolved_at": str((a or ["", ""])[1] or ""),
            "policy_count": int((p or [0])[0]),
            "rollout": {
                "status": str((r or ["", 0, ""])[0] or ""),
                "current_percent": int((r or ["", 0, ""])[1] or 0),
                "router_mode": str((r or ["", 0, ""])[2] or ""),
            },
        }
    finally:
        conn.close()


def _upsert_temp_policy(policy_id: str) -> None:
    now = _ts()
    conn = sqlite3.connect(str(MLOPS_DB), timeout=20)
    try:
        conn.execute(
            """
            INSERT INTO scenario_router_auto_recovery_policies (
                policy_id, alert_type, severity, action_type,
                auto_execute, require_confirm, enabled,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(policy_id) DO UPDATE SET
                alert_type = excluded.alert_type,
                severity = excluded.severity,
                action_type = excluded.action_type,
                auto_execute = excluded.auto_execute,
                require_confirm = excluded.require_confirm,
                enabled = excluded.enabled,
                updated_at = excluded.updated_at
            """,
            (
                policy_id,
                "RUN_FAILED",
                "WARNING",
                "RUN_CANARY_EVALUATE",
                1,
                0,
                1,
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _cleanup(alert_id: str, policy_id: str) -> None:
    conn = sqlite3.connect(str(MLOPS_DB), timeout=20)
    try:
        conn.execute("DELETE FROM scenario_router_auto_recovery_executions WHERE alert_id = ?", (alert_id,))
        conn.execute("DELETE FROM scenario_router_incident_actions WHERE alert_id = ?", (alert_id,))
        conn.execute("DELETE FROM scenario_router_incident_responses WHERE alert_id = ?", (alert_id,))
        conn.execute("DELETE FROM scenario_router_runbooks WHERE alert_id = ?", (alert_id,))
        conn.execute("DELETE FROM scenario_router_notification_deliveries WHERE alert_id = ?", (alert_id,))
        conn.execute("DELETE FROM scenario_router_alert_events WHERE alert_id = ?", (alert_id,))
        conn.execute("DELETE FROM scenario_router_alerts WHERE alert_id = ?", (alert_id,))
        conn.execute("DELETE FROM scenario_router_auto_recovery_policies WHERE policy_id = ?", (policy_id,))
        conn.commit()
    finally:
        conn.close()


def main() -> int:
    if not MLOPS_DB.exists():
        _print_check("db_exists", False, f"missing {MLOPS_DB}")
        return 1

    headers = _auth_headers()
    failures = 0
    alert_id = ""
    temp_policy_id = f"srarp_e2e_{uuid.uuid4().hex[:10]}"

    try:
        with httpx.Client(timeout=TIMEOUT_SEC) as client:
            health = client.get(f"{BASE_URL}/health")
            ok_health = health.status_code == 200
            _print_check("health", ok_health, f"status={health.status_code}")
            if not ok_health:
                return 1

            # Quick auth probe against admin endpoint.
            probe = _post(
                client,
                "/api/mlops/research/scenario-router/auto-recovery/evaluate",
                {"alert_id": "_probe_missing_"},
                headers,
            )
            if probe.status_code in {401, 403}:
                _print_check(
                    "admin_auth",
                    False,
                    "set E2E_BEARER_TOKEN or E2E_USE_FAKE_ADMIN_TOKEN=1",
                )
                return 1
            _print_check("admin_auth", True, f"probe_status={probe.status_code}")

            # 1) Create temporary STOP_CANARY alert.
            alert_id = _create_temp_alert()
            _print_check("setup_alert", bool(alert_id), alert_id)

            # Stabilize this smoke regardless of existing local policies.
            _upsert_temp_policy(temp_policy_id)
            _print_check("setup_policy", True, temp_policy_id)

            # 2) Prepare incident response package via HTTP.
            prepare = _post(
                client,
                "/api/mlops/research/scenario-router/incidents/response/prepare",
                {
                    "alert_id": alert_id,
                    "save_response": True,
                    "include_runbook_summary": True,
                    "notification_channel_type": "slack",
                    "include_action_preview": True,
                },
                headers,
            )
            ok_prepare = prepare.status_code == 200
            _print_check("prepare_response_api", ok_prepare, f"status={prepare.status_code}")
            if not ok_prepare:
                failures += 1
                print(prepare.text[:500])
                return 1

            prep_data = prepare.json() or {}
            response_id = str(prep_data.get("response_id") or "")
            _print_check("prepare_response_id", bool(response_id), response_id)
            if not response_id:
                failures += 1
                return 1

            # 3) evaluate API returns plan.
            eval_resp = _post(
                client,
                "/api/mlops/research/scenario-router/auto-recovery/evaluate",
                {
                    "response_id": response_id,
                    "include_action_preview": True,
                    "include_runbook_summary": True,
                    "notification_channel_type": "slack",
                },
                headers,
            )
            ok_eval = eval_resp.status_code == 200
            _print_check("evaluate_api", ok_eval, f"status={eval_resp.status_code}")
            if not ok_eval:
                failures += 1
                print(eval_resp.text[:500])
                return 1

            eval_data = eval_resp.json() or {}
            plan = eval_data.get("plan") if isinstance(eval_data.get("plan"), list) else []
            _print_check("1_plan_generated", len(plan) > 0, f"plan_size={len(plan)}")
            if not plan:
                failures += 1

            by_action: dict[str, dict[str, Any]] = {}
            for p in plan:
                if isinstance(p, dict):
                    by_action[str(p.get("action_type") or "")] = p

            # 4) RUN_CANARY_EVALUATE is auto_execute candidate.
            run_canary = by_action.get("RUN_CANARY_EVALUATE") or {}
            ok_run_canary = bool(run_canary) and bool(run_canary.get("auto_execute"))
            _print_check("2_run_canary_auto_execute", ok_run_canary)
            if not ok_run_canary:
                failures += 1

            # 5) Dangerous actions are manual_required.
            stop_canary = by_action.get("STOP_CANARY") or {}
            rollback = by_action.get("ROLLBACK_TO_SHADOW") or {}
            ok_danger_manual = (
                bool(stop_canary)
                and bool(rollback)
                and bool(stop_canary.get("manual_required"))
                and bool(rollback.get("manual_required"))
                and not bool(stop_canary.get("auto_execute"))
                and not bool(rollback.get("auto_execute"))
            )
            _print_check("3_danger_manual_required", ok_danger_manual)
            if not ok_danger_manual:
                failures += 1

            snap_before = _snapshot_state(alert_id)

            # 6) execute dry-run keeps state unchanged.
            dry_resp = _post(
                client,
                "/api/mlops/research/scenario-router/auto-recovery/execute",
                {
                    "response_id": response_id,
                    "apply_updates": False,
                    "confirm": False,
                    "requested_by": "e2e-smoke",
                    "approved_by": "e2e-smoke",
                    "include_action_preview": True,
                    "include_runbook_summary": True,
                    "notification_channel_type": "slack",
                },
                headers,
            )
            ok_dry_http = dry_resp.status_code == 200
            _print_check("execute_dry_http", ok_dry_http, f"status={dry_resp.status_code}")
            if not ok_dry_http:
                failures += 1
                print(dry_resp.text[:500])
                return 1

            dry_data = dry_resp.json() or {}
            dry_results = dry_data.get("results") if isinstance(dry_data.get("results"), list) else []
            dry_statuses = {str(x.get("status") or "") for x in dry_results if isinstance(x, dict)}
            snap_after_dry = _snapshot_state(alert_id)
            dry_no_state_change = (
                snap_before.get("alert_status") == snap_after_dry.get("alert_status")
                and snap_before.get("alert_resolved_at") == snap_after_dry.get("alert_resolved_at")
                and snap_before.get("policy_count") == snap_after_dry.get("policy_count")
                and snap_before.get("rollout") == snap_after_dry.get("rollout")
            )
            ok_dry = dry_statuses == {"DRY_RUN"} and dry_no_state_change
            _print_check(
                "4_dry_run_no_state_change",
                ok_dry,
                f"statuses={sorted(dry_statuses)}",
            )
            if not ok_dry:
                failures += 1

            # 7) execute apply=true runs safe only, dangerous remain skipped/blocked.
            apply_resp = _post(
                client,
                "/api/mlops/research/scenario-router/auto-recovery/execute",
                {
                    "response_id": response_id,
                    "apply_updates": True,
                    "confirm": False,
                    "requested_by": "e2e-smoke",
                    "approved_by": "e2e-smoke",
                    "include_action_preview": True,
                    "include_runbook_summary": True,
                    "notification_channel_type": "slack",
                },
                headers,
            )
            ok_apply_http = apply_resp.status_code == 200
            _print_check("execute_apply_http", ok_apply_http, f"status={apply_resp.status_code}")
            if not ok_apply_http:
                failures += 1
                print(apply_resp.text[:500])
                return 1

            apply_data = apply_resp.json() or {}
            apply_results = apply_data.get("results") if isinstance(apply_data.get("results"), list) else []
            by_action_apply: dict[str, str] = {}
            for r in apply_results:
                if isinstance(r, dict):
                    by_action_apply[str(r.get("action_type") or "")] = str(r.get("status") or "")

            has_safe_exec = any(
                by_action_apply.get(a) == "EXECUTED"
                for a in ["RUN_CANARY_EVALUATE", "RUN_ROUTER_BACKTEST", "RUN_E2E_VALIDATION", "NOTIFICATION_DISPATCH", "RESOLVE_ALERT"]
            )
            danger_not_executed = all(
                by_action_apply.get(a) in {"SKIPPED", "BLOCKED", ""}
                for a in ["STOP_CANARY", "ROLLBACK_TO_SHADOW", "DISABLE_POLICY", "LOWER_POLICY_PRIORITY"]
            )
            _print_check("5_safe_only_execute", has_safe_exec)
            _print_check("6_danger_not_executed_without_confirm", danger_not_executed)
            if not has_safe_exec:
                failures += 1
            if not danger_not_executed:
                failures += 1

            # 8) executions API returns history.
            hist_resp = _get(
                client,
                "/api/mlops/research/scenario-router/auto-recovery/executions",
                {"alert_id": alert_id, "limit": 200},
                headers,
            )
            ok_hist_http = hist_resp.status_code == 200
            _print_check("executions_api", ok_hist_http, f"status={hist_resp.status_code}")
            if not ok_hist_http:
                failures += 1
                print(hist_resp.text[:500])
                return 1

            hist_data = hist_resp.json() or {}
            items = hist_data.get("items") if isinstance(hist_data.get("items"), list) else []
            hist_statuses = {str(x.get("status") or "") for x in items if isinstance(x, dict)}
            ok_hist = len(items) > 0 and ("DRY_RUN" in hist_statuses) and (
                "EXECUTED" in hist_statuses or "SKIPPED" in hist_statuses or "BLOCKED" in hist_statuses
            )
            _print_check("7_history_saved", ok_hist, f"count={len(items)} statuses={sorted(hist_statuses)}")
            if not ok_hist:
                failures += 1

    finally:
        cleanup_ok = False
        if alert_id:
            try:
                _cleanup(alert_id, temp_policy_id)
                cleanup_ok = True
            except Exception as e:
                cleanup_ok = False
                print(f"cleanup_error={e}")
        _print_check("8_cleanup", cleanup_ok)

    if failures > 0:
        print(f"RESULT: FAIL ({failures} checks failed)")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
