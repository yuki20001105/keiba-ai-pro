from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_db() -> Path:
    override = (
        str(os.environ.get("SCENARIO_ROUTER_AUDIT_DB_PATH") or "").strip()
        or str(os.environ.get("MLOPS_DB_PATH") or "").strip()
    )
    if override:
        return Path(override)
    return _repo_root() / "keiba" / "data" / "mlops.db"


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


class MLOpsStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or _default_db()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        conn = self._connect()

        def _ensure_column(table: str, column: str, ddl: str) -> None:
            try:
                cols = {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                if column not in cols:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
            except Exception:
                pass

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS experiment_runs (
                experiment_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                target TEXT NOT NULL,
                model_type TEXT NOT NULL,
                git_hash TEXT,
                dataset_from TEXT,
                dataset_to TEXT,
                feature_store_version TEXT,
                feature_quality_score REAL,
                params_json TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                artifacts_json TEXT NOT NULL,
                status TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS model_registry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                target TEXT NOT NULL,
                status TEXT NOT NULL,
                stage TEXT NOT NULL,
                experiment_id TEXT,
                dataset_from TEXT,
                dataset_to TEXT,
                feature_store_version TEXT,
                feature_quality_score REAL,
                metrics_json TEXT NOT NULL,
                notes TEXT,
                promoted_at TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mr_model_id ON model_registry(model_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mr_stage ON model_registry(stage)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_er_created_at ON experiment_runs(created_at)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS prediction_runs (
                prediction_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                race_id TEXT NOT NULL,
                race_date TEXT,
                model_id TEXT NOT NULL,
                experiment_id TEXT,
                feature_store_version TEXT,
                prediction_version TEXT,
                quality_gate_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                scenario_id TEXT,
                scenario_hash TEXT,
                scenario_features_json TEXT,
                scenario_reason TEXT,
                scenario_confidence REAL,
                winning_pattern TEXT,
                expected_pace TEXT,
                expected_bias TEXT,
                selected_model_id TEXT,
                route_type TEXT,
                matched_scenario_key TEXT,
                matched_scenario_value TEXT,
                router_reason TEXT,
                fallback_used INTEGER DEFAULT 0,
                router_mode TEXT,
                actual_model_id TEXT,
                shadow_selected_model_id TEXT,
                shadow_route_type TEXT,
                shadow_matched_scenario_key TEXT,
                shadow_matched_scenario_value TEXT,
                shadow_router_reason TEXT,
                shadow_fallback_used INTEGER DEFAULT 0,
                canary_percent INTEGER,
                canary_bucket INTEGER,
                canary_selected INTEGER DEFAULT 0,
                effective_router_mode TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS prediction_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prediction_id TEXT NOT NULL,
                horse_id TEXT,
                horse_number INTEGER,
                horse_name TEXT,
                score REAL,
                probability REAL,
                calibrated_probability REAL,
                rank INTEGER,
                expected_value REAL,
                odds REAL,
                buy_flag INTEGER DEFAULT 0,
                reason TEXT,
                confidence REAL,
                scenario_fit REAL
            )
            """
        )
        _ensure_column("prediction_runs", "scenario_id", "scenario_id TEXT")
        _ensure_column("prediction_runs", "scenario_hash", "scenario_hash TEXT")
        _ensure_column("prediction_runs", "scenario_features_json", "scenario_features_json TEXT")
        _ensure_column("prediction_runs", "scenario_reason", "scenario_reason TEXT")
        _ensure_column("prediction_runs", "scenario_confidence", "scenario_confidence REAL")
        _ensure_column("prediction_runs", "winning_pattern", "winning_pattern TEXT")
        _ensure_column("prediction_runs", "expected_pace", "expected_pace TEXT")
        _ensure_column("prediction_runs", "expected_bias", "expected_bias TEXT")
        _ensure_column("prediction_runs", "selected_model_id", "selected_model_id TEXT")
        _ensure_column("prediction_runs", "route_type", "route_type TEXT")
        _ensure_column("prediction_runs", "matched_scenario_key", "matched_scenario_key TEXT")
        _ensure_column("prediction_runs", "matched_scenario_value", "matched_scenario_value TEXT")
        _ensure_column("prediction_runs", "router_reason", "router_reason TEXT")
        _ensure_column("prediction_runs", "fallback_used", "fallback_used INTEGER DEFAULT 0")
        _ensure_column("prediction_runs", "router_mode", "router_mode TEXT")
        _ensure_column("prediction_runs", "actual_model_id", "actual_model_id TEXT")
        _ensure_column("prediction_runs", "shadow_selected_model_id", "shadow_selected_model_id TEXT")
        _ensure_column("prediction_runs", "shadow_route_type", "shadow_route_type TEXT")
        _ensure_column("prediction_runs", "shadow_matched_scenario_key", "shadow_matched_scenario_key TEXT")
        _ensure_column("prediction_runs", "shadow_matched_scenario_value", "shadow_matched_scenario_value TEXT")
        _ensure_column("prediction_runs", "shadow_router_reason", "shadow_router_reason TEXT")
        _ensure_column("prediction_runs", "shadow_fallback_used", "shadow_fallback_used INTEGER DEFAULT 0")
        _ensure_column("prediction_runs", "canary_percent", "canary_percent INTEGER")
        _ensure_column("prediction_runs", "canary_bucket", "canary_bucket INTEGER")
        _ensure_column("prediction_runs", "canary_selected", "canary_selected INTEGER DEFAULT 0")
        _ensure_column("prediction_runs", "effective_router_mode", "effective_router_mode TEXT")
        _ensure_column("prediction_results", "reason", "reason TEXT")
        _ensure_column("prediction_results", "confidence", "confidence REAL")
        _ensure_column("prediction_results", "scenario_fit", "scenario_fit REAL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pr_prediction ON prediction_results(prediction_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pr_race ON prediction_runs(race_id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bet_registry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                prediction_id TEXT,
                race_id TEXT NOT NULL,
                bet_type TEXT NOT NULL,
                combinations_json TEXT NOT NULL,
                unit_price INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                total_cost INTEGER NOT NULL,
                expected_return REAL,
                odds_json TEXT,
                status TEXT NOT NULL DEFAULT 'planned',
                payout REAL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bet_race ON bet_registry(race_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bet_prediction ON bet_registry(prediction_id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS evaluation_registry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                prediction_id TEXT NOT NULL,
                race_id TEXT NOT NULL,
                model_id TEXT NOT NULL,
                hit_rate REAL,
                roi REAL,
                calibration_error REAL,
                top1_hit INTEGER,
                top3_hit INTEGER,
                metrics_json TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_eval_prediction ON evaluation_registry(prediction_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_eval_race ON evaluation_registry(race_id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scenario_adoption_decisions (
                decision_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                experiment_id TEXT,
                baseline_model_id TEXT NOT NULL,
                challenger_model_id TEXT NOT NULL,
                scenario_key TEXT NOT NULL,
                scenario_value TEXT NOT NULL,
                decision TEXT NOT NULL,
                reason TEXT,
                roi_lift REAL,
                hit_rate_lift REAL,
                top3_lift REAL,
                ev_lift REAL,
                p_value REAL,
                p_value_fdr REAL,
                ci_lower REAL,
                ci_upper REAL,
                overlap INTEGER,
                details_json TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sad_created ON scenario_adoption_decisions(created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sad_models ON scenario_adoption_decisions(baseline_model_id, challenger_model_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sad_segment ON scenario_adoption_decisions(scenario_key, scenario_value)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scenario_model_policies (
                policy_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                scenario_key TEXT NOT NULL,
                scenario_value TEXT NOT NULL,
                model_id TEXT NOT NULL,
                feature_set_id TEXT,
                strategy_id TEXT,
                priority INTEGER NOT NULL DEFAULT 100,
                confidence REAL,
                status TEXT NOT NULL,
                source_decision_id TEXT,
                notes TEXT
            )
            """
        )
        _ensure_column("scenario_model_policies", "confidence", "confidence REAL")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_smp_segment ON scenario_model_policies(scenario_key, scenario_value, status, priority)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_smp_model ON scenario_model_policies(model_id, status)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scenario_policy_evaluations (
                evaluation_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                policy_id TEXT NOT NULL,
                scenario_key TEXT NOT NULL,
                scenario_value TEXT NOT NULL,
                model_id TEXT NOT NULL,
                races INTEGER,
                router_roi REAL,
                global_roi REAL,
                roi_lift REAL,
                hit_rate_lift REAL,
                action TEXT NOT NULL,
                reason TEXT,
                details_json TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_spe_policy_created ON scenario_policy_evaluations(policy_id, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_spe_segment_created ON scenario_policy_evaluations(scenario_key, scenario_value, created_at)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scenario_policy_lifecycle (
                policy_id TEXT PRIMARY KEY,
                last_action TEXT,
                consecutive_keep INTEGER NOT NULL DEFAULT 0,
                consecutive_raise INTEGER NOT NULL DEFAULT 0,
                consecutive_lower INTEGER NOT NULL DEFAULT 0,
                consecutive_disable INTEGER NOT NULL DEFAULT 0,
                consecutive_watch INTEGER NOT NULL DEFAULT 0,
                consecutive_needs_more_data INTEGER NOT NULL DEFAULT 0,
                cooldown_until TEXT,
                lifecycle_status TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_spl_status_updated ON scenario_policy_lifecycle(lifecycle_status, updated_at)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scenario_router_rollouts (
                rollout_id TEXT PRIMARY KEY,
                target TEXT NOT NULL,
                current_percent INTEGER NOT NULL DEFAULT 0,
                previous_percent INTEGER NOT NULL DEFAULT 0,
                router_mode TEXT NOT NULL DEFAULT 'shadow',
                status TEXT NOT NULL DEFAULT 'SHADOW_ONLY',
                last_decision TEXT,
                last_reason TEXT,
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        _ensure_column("scenario_router_rollouts", "target", "target TEXT")
        _ensure_column("scenario_router_rollouts", "current_percent", "current_percent INTEGER NOT NULL DEFAULT 0")
        _ensure_column("scenario_router_rollouts", "previous_percent", "previous_percent INTEGER NOT NULL DEFAULT 0")
        _ensure_column("scenario_router_rollouts", "router_mode", "router_mode TEXT NOT NULL DEFAULT 'shadow'")
        _ensure_column("scenario_router_rollouts", "status", "status TEXT NOT NULL DEFAULT 'SHADOW_ONLY'")
        _ensure_column("scenario_router_rollouts", "last_decision", "last_decision TEXT")
        _ensure_column("scenario_router_rollouts", "last_reason", "last_reason TEXT")
        _ensure_column("scenario_router_rollouts", "started_at", "started_at TEXT")
        _ensure_column("scenario_router_rollouts", "updated_at", "updated_at TEXT")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_srr_target ON scenario_router_rollouts(target)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_srr_status_updated ON scenario_router_rollouts(status, updated_at)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scenario_router_rollout_events (
                event_id TEXT PRIMARY KEY,
                rollout_id TEXT NOT NULL,
                target TEXT NOT NULL,
                from_percent INTEGER NOT NULL,
                to_percent INTEGER NOT NULL,
                decision TEXT NOT NULL,
                action TEXT NOT NULL,
                reason TEXT,
                summary_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        _ensure_column("scenario_router_rollout_events", "target", "target TEXT")
        _ensure_column("scenario_router_rollout_events", "summary_json", "summary_json TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_srre_rollout_created ON scenario_router_rollout_events(rollout_id, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_srre_target_created ON scenario_router_rollout_events(target, created_at)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scenario_router_rollout_runs (
                run_id TEXT PRIMARY KEY,
                target TEXT NOT NULL,
                date_from TEXT,
                date_to TEXT,
                decision TEXT,
                action TEXT,
                from_percent INTEGER,
                to_percent INTEGER,
                apply_updates INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                error_message TEXT,
                summary_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        _ensure_column("scenario_router_rollout_runs", "summary_json", "summary_json TEXT")
        _ensure_column("scenario_router_rollout_runs", "error_message", "error_message TEXT")
        _ensure_column("scenario_router_rollout_runs", "status", "status TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_srrr_target_created ON scenario_router_rollout_runs(target, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_srrr_status_created ON scenario_router_rollout_runs(status, created_at)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scenario_router_alerts (
                alert_id TEXT PRIMARY KEY,
                target TEXT NOT NULL,
                severity TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                status TEXT NOT NULL,
                title TEXT,
                message TEXT,
                source_run_id TEXT,
                decision TEXT,
                action TEXT,
                summary_json TEXT,
                created_at TEXT NOT NULL,
                resolved_at TEXT
            )
            """
        )
        _ensure_column("scenario_router_alerts", "resolved_at", "resolved_at TEXT")
        _ensure_column("scenario_router_alerts", "summary_json", "summary_json TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sra_target_status_created ON scenario_router_alerts(target, status, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sra_type_status ON scenario_router_alerts(alert_type, status)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scenario_router_alert_events (
                event_id TEXT PRIMARY KEY,
                alert_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                message TEXT,
                payload_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        _ensure_column("scenario_router_alert_events", "payload_json", "payload_json TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_srae_alert_created ON scenario_router_alert_events(alert_id, created_at)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scenario_router_notification_channels (
                channel_id TEXT PRIMARY KEY,
                channel_type TEXT NOT NULL,
                name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                config_json TEXT,
                severity_filter TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        _ensure_column("scenario_router_notification_channels", "config_json", "config_json TEXT")
        _ensure_column("scenario_router_notification_channels", "severity_filter", "severity_filter TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_srnc_type_enabled ON scenario_router_notification_channels(channel_type, enabled)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS scenario_router_notification_deliveries ("
            "delivery_id TEXT PRIMARY KEY,"
            "alert_id TEXT NOT NULL,"
            "channel_id TEXT NOT NULL,"
            "status TEXT NOT NULL,"
            "attempt_count INTEGER NOT NULL DEFAULT 1,"
            "last_error TEXT,"
            "payload_json TEXT,"
            "sent_at TEXT,"
            "created_at TEXT NOT NULL"
            ")"
        )
        _ensure_column("scenario_router_notification_deliveries", "last_error", "last_error TEXT")
        _ensure_column("scenario_router_notification_deliveries", "payload_json", "payload_json TEXT")
        _ensure_column("scenario_router_notification_deliveries", "sent_at", "sent_at TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_srnd_alert_channel_status ON scenario_router_notification_deliveries(alert_id, channel_id, status, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_srnd_status_created ON scenario_router_notification_deliveries(status, created_at)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scenario_router_runbooks (
                runbook_id TEXT PRIMARY KEY,
                alert_id TEXT NOT NULL,
                target TEXT NOT NULL,
                severity TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                title TEXT,
                summary TEXT,
                root_cause_hypotheses_json TEXT,
                checklist_json TEXT,
                recommended_actions_json TEXT,
                related_apis_json TEXT,
                recovery_conditions_json TEXT,
                observed_metrics_json TEXT,
                threshold_comparison_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        _ensure_column("scenario_router_runbooks", "observed_metrics_json", "observed_metrics_json TEXT")
        _ensure_column("scenario_router_runbooks", "threshold_comparison_json", "threshold_comparison_json TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_srrb_alert_created ON scenario_router_runbooks(alert_id, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_srrb_target_created ON scenario_router_runbooks(target, created_at)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scenario_router_incident_actions (
                action_id TEXT PRIMARY KEY,
                alert_id TEXT,
                runbook_id TEXT,
                target TEXT NOT NULL,
                action_type TEXT NOT NULL,
                status TEXT NOT NULL,
                dry_run INTEGER NOT NULL DEFAULT 0,
                requested_by TEXT,
                approved_by TEXT,
                result_json TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                executed_at TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sria_alert_action_status ON scenario_router_incident_actions(alert_id, action_type, status, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sria_target_created ON scenario_router_incident_actions(target, created_at)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scenario_router_incident_responses (
                response_id TEXT PRIMARY KEY,
                alert_id TEXT NOT NULL,
                runbook_id TEXT,
                target TEXT NOT NULL,
                severity TEXT NOT NULL,
                status TEXT NOT NULL,
                recommended_actions_json TEXT,
                notification_preview_json TEXT,
                summary_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_srir_alert_created ON scenario_router_incident_responses(alert_id, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_srir_target_status_updated ON scenario_router_incident_responses(target, status, updated_at)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scenario_router_auto_recovery_policies (
                policy_id TEXT PRIMARY KEY,
                alert_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                action_type TEXT NOT NULL,
                auto_execute INTEGER NOT NULL DEFAULT 0,
                require_confirm INTEGER NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_srarp_lookup ON scenario_router_auto_recovery_policies(alert_type, severity, action_type, enabled)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scenario_router_auto_recovery_executions (
                execution_id TEXT PRIMARY KEY,
                response_id TEXT,
                alert_id TEXT,
                action_type TEXT NOT NULL,
                status TEXT NOT NULL,
                auto_executed INTEGER NOT NULL DEFAULT 0,
                manual_required INTEGER NOT NULL DEFAULT 0,
                result_json TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                executed_at TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_srare_response_created ON scenario_router_auto_recovery_executions(response_id, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_srare_alert_action_status ON scenario_router_auto_recovery_executions(alert_id, action_type, status, created_at)"
        )
        conn.commit()
        conn.close()

    def get_latest_model_meta(self, model_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        row = conn.execute(
            """
            SELECT id, model_id, target, experiment_id, dataset_from, dataset_to,
                   feature_store_version, feature_quality_score, stage
            FROM model_registry
            WHERE model_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (model_id,),
        ).fetchone()
        conn.close()
        if not row:
            return None
        return {
            "id": int(row[0]),
            "model_id": str(row[1]),
            "target": str(row[2]),
            "experiment_id": str(row[3] or ""),
            "dataset_from": str(row[4] or ""),
            "dataset_to": str(row[5] or ""),
            "feature_store_version": str(row[6] or ""),
            "feature_quality_score": (float(row[7]) if row[7] is not None else None),
            "stage": str(row[8] or ""),
        }

    def get_global_champion_model(self, target: str | None = None) -> str | None:
        conn = self._connect()
        if target:
            row = conn.execute(
                """
                SELECT model_id
                FROM model_registry
                WHERE stage = 'production' AND target = ?
                ORDER BY COALESCE(promoted_at, created_at) DESC, created_at DESC
                LIMIT 1
                """,
                (target,),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT model_id
                FROM model_registry
                WHERE stage = 'production'
                ORDER BY COALESCE(promoted_at, created_at) DESC, created_at DESC
                LIMIT 1
                """,
            ).fetchone()
        conn.close()
        if not row:
            return None
        return str(row[0] or "") or None

    def find_scenario_model_policies(
        self,
        *,
        scenario: dict[str, str],
        status: str = "active",
    ) -> list[dict[str, Any]]:
        keys = [
            ("expected_pace", str(scenario.get("expected_pace") or "")),
            ("expected_bias", str(scenario.get("expected_bias") or "")),
            ("winning_pattern", str(scenario.get("winning_pattern") or "")),
        ]
        clauses: list[str] = []
        params: list[Any] = [status]
        for k, v in keys:
            if not v:
                continue
            clauses.append("(scenario_key = ? AND scenario_value = ?)")
            params.extend([k, v])

        if not clauses:
            return []

        sql = f"""
            SELECT policy_id, created_at, updated_at,
                   scenario_key, scenario_value,
                   model_id, feature_set_id, strategy_id,
                   priority, confidence, status,
                   source_decision_id, notes
            FROM scenario_model_policies
            WHERE status = ? AND ({' OR '.join(clauses)})
            ORDER BY priority DESC, COALESCE(confidence, 0.0) DESC, updated_at DESC
        """

        conn = self._connect()
        rows = conn.execute(sql, params).fetchall()
        conn.close()

        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "policy_id": str(r[0] or ""),
                    "created_at": str(r[1] or ""),
                    "updated_at": str(r[2] or ""),
                    "scenario_key": str(r[3] or ""),
                    "scenario_value": str(r[4] or ""),
                    "model_id": str(r[5] or ""),
                    "feature_set_id": str(r[6] or ""),
                    "strategy_id": str(r[7] or ""),
                    "priority": int(r[8] or 0),
                    "confidence": (float(r[9]) if r[9] is not None else None),
                    "status": str(r[10] or ""),
                    "source_decision_id": str(r[11] or ""),
                    "notes": str(r[12] or ""),
                }
            )
        return out

    def get_model_ids_by_target(self, target: str | None) -> list[str]:
        if not target:
            return []
        conn = self._connect()
        rows = conn.execute(
            """
            SELECT DISTINCT model_id
            FROM model_registry
            WHERE target = ?
            """,
            (target,),
        ).fetchall()
        conn.close()
        return [str(r[0]) for r in rows if r and r[0] is not None]

    def fetch_router_backtest_top_predictions(
        self,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        target: str | None = None,
        router_mode: str = "active",
    ) -> dict[str, Any]:
        model_ids = self.get_model_ids_by_target(target)
        mode = str(router_mode or "active").strip().lower()
        if mode not in {"active", "shadow"}:
            mode = "active"

        where: list[str] = []
        params: list[Any] = []
        if date_from:
            where.append("pr.race_date >= ?")
            params.append(str(date_from))
        if date_to:
            where.append("pr.race_date <= ?")
            params.append(str(date_to))
        if model_ids:
            ph = ",".join(["?"] * len(model_ids))
            where.append(f"pr.model_id IN ({ph})")
            params.extend(model_ids)
        if mode == "shadow":
            where.append("COALESCE(pr.router_mode, 'off') = ?")
            params.append("shadow")
        else:
            where.append("COALESCE(pr.router_mode, 'active') = ?")
            params.append("active")

        where_sql = " WHERE " + " AND ".join([*where, "rs.rank = 1"])

        conn = self._connect()
        rows = conn.execute(
            f"""
            SELECT pr.prediction_id, pr.race_id, pr.race_date,
                   pr.model_id, pr.selected_model_id, pr.route_type,
                   pr.matched_scenario_key, pr.matched_scenario_value,
                   pr.router_reason, pr.fallback_used,
                   pr.router_mode, pr.actual_model_id,
                   pr.shadow_selected_model_id, pr.shadow_route_type,
                   pr.shadow_matched_scenario_key, pr.shadow_matched_scenario_value,
                   pr.shadow_router_reason, pr.shadow_fallback_used,
                   pr.expected_pace, pr.expected_bias, pr.winning_pattern,
                   rs.horse_id, rs.odds, rs.expected_value
            FROM prediction_runs pr
            JOIN prediction_results rs ON rs.prediction_id = pr.prediction_id
            {where_sql}
            ORDER BY pr.created_at DESC
            """,
            params,
        ).fetchall()

        missing_where = list(where)
        missing_params = list(params)
        if mode == "shadow":
            missing_cond = (
                "(pr.shadow_route_type IS NULL OR pr.shadow_route_type = '' "
                "OR pr.shadow_selected_model_id IS NULL OR pr.shadow_selected_model_id = '')"
            )
        else:
            missing_cond = (
                "(pr.route_type IS NULL OR pr.route_type = '' "
                "OR pr.selected_model_id IS NULL OR pr.selected_model_id = '')"
            )
        missing_sql = " AND ".join([*missing_where, missing_cond]) if missing_where else missing_cond
        missing_routing = conn.execute(
            f"SELECT COUNT(*) FROM prediction_runs pr WHERE {missing_sql}",
            missing_params,
        ).fetchone()

        conn.close()

        out_rows: list[dict[str, Any]] = []
        for r in rows:
            out_rows.append(
                {
                    "prediction_id": str(r[0] or ""),
                    "race_id": str(r[1] or ""),
                    "race_date": str(r[2] or ""),
                    "model_id": str(r[3] or ""),
                    "selected_model_id": str(r[4] or ""),
                    "route_type": str(r[5] or ""),
                    "matched_scenario_key": str(r[6] or ""),
                    "matched_scenario_value": str(r[7] or ""),
                    "router_reason": str(r[8] or ""),
                    "fallback_used": bool(int(r[9] or 0)),
                    "router_mode": str(r[10] or ""),
                    "actual_model_id": str(r[11] or ""),
                    "shadow_selected_model_id": str(r[12] or ""),
                    "shadow_route_type": str(r[13] or ""),
                    "shadow_matched_scenario_key": str(r[14] or ""),
                    "shadow_matched_scenario_value": str(r[15] or ""),
                    "shadow_router_reason": str(r[16] or ""),
                    "shadow_fallback_used": bool(int(r[17] or 0)),
                    "expected_pace": str(r[18] or ""),
                    "expected_bias": str(r[19] or ""),
                    "winning_pattern": str(r[20] or ""),
                    "horse_id": str(r[21] or ""),
                    "odds": (float(r[22]) if r[22] is not None else None),
                    "expected_value": (float(r[23]) if r[23] is not None else None),
                }
            )

        return {
            "rows": out_rows,
            "missing_routing_count": int((missing_routing or [0])[0]),
            "target_model_count": int(len(model_ids)) if target else 0,
            "router_mode": mode,
        }

    def fetch_top_predictions_for_model_and_races(
        self,
        *,
        model_id: str,
        race_ids: list[str],
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict[str, Any]]:
        mid = str(model_id or "").strip()
        if not mid or not race_ids:
            return []

        conn = self._connect()
        out: list[dict[str, Any]] = []
        seen_race: set[str] = set()

        chunk_size = 600
        for i in range(0, len(race_ids), chunk_size):
            chunk = [str(x) for x in race_ids[i : i + chunk_size] if str(x)]
            if not chunk:
                continue
            ph = ",".join(["?"] * len(chunk))
            where = ["pr.model_id = ?", f"pr.race_id IN ({ph})", "rs.rank = 1"]
            params: list[Any] = [mid, *chunk]
            if date_from:
                where.append("pr.race_date >= ?")
                params.append(str(date_from))
            if date_to:
                where.append("pr.race_date <= ?")
                params.append(str(date_to))
            rows = conn.execute(
                f"""
                SELECT pr.prediction_id, pr.race_id, pr.race_date,
                       pr.model_id,
                       rs.horse_id, rs.odds, rs.expected_value
                FROM prediction_runs pr
                JOIN prediction_results rs ON rs.prediction_id = pr.prediction_id
                WHERE {' AND '.join(where)}
                ORDER BY pr.created_at DESC
                """,
                params,
            ).fetchall()
            for r in rows:
                rid = str(r[1] or "")
                if not rid or rid in seen_race:
                    continue
                seen_race.add(rid)
                out.append(
                    {
                        "prediction_id": str(r[0] or ""),
                        "race_id": rid,
                        "race_date": str(r[2] or ""),
                        "model_id": str(r[3] or ""),
                        "horse_id": str(r[4] or ""),
                        "odds": (float(r[5]) if r[5] is not None else None),
                        "expected_value": (float(r[6]) if r[6] is not None else None),
                    }
                )
        conn.close()
        return out

    def fetch_top_predictions_for_model_race_pairs(
        self,
        *,
        model_race_pairs: list[tuple[str, str]],
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict[str, Any]]:
        pairs = [(str(m), str(r)) for m, r in (model_race_pairs or []) if str(m) and str(r)]
        if not pairs:
            return []
        conn = self._connect()
        out: list[dict[str, Any]] = []
        for mid, rid in pairs:
            where = ["pr.model_id = ?", "pr.race_id = ?", "rs.rank = 1"]
            params: list[Any] = [mid, rid]
            if date_from:
                where.append("pr.race_date >= ?")
                params.append(str(date_from))
            if date_to:
                where.append("pr.race_date <= ?")
                params.append(str(date_to))
            row = conn.execute(
                f"""
                SELECT pr.prediction_id, pr.race_id, pr.race_date,
                       pr.model_id, rs.horse_id, rs.odds, rs.expected_value
                FROM prediction_runs pr
                JOIN prediction_results rs ON rs.prediction_id = pr.prediction_id
                WHERE {' AND '.join(where)}
                ORDER BY pr.created_at DESC
                LIMIT 1
                """,
                params,
            ).fetchone()
            if not row:
                continue
            out.append(
                {
                    "prediction_id": str(row[0] or ""),
                    "race_id": str(row[1] or ""),
                    "race_date": str(row[2] or ""),
                    "model_id": str(row[3] or ""),
                    "horse_id": str(row[4] or ""),
                    "odds": (float(row[5]) if row[5] is not None else None),
                    "expected_value": (float(row[6]) if row[6] is not None else None),
                }
            )
        conn.close()
        return out

    def fetch_top3_hit_flags(self, *, prediction_ids: list[str], winner_by_race: dict[str, str]) -> dict[str, int]:
        if not prediction_ids:
            return {}
        conn = self._connect()
        out: dict[str, int] = {str(pid): 0 for pid in prediction_ids}
        chunk_size = 700
        for i in range(0, len(prediction_ids), chunk_size):
            chunk = [str(x) for x in prediction_ids[i : i + chunk_size] if str(x)]
            if not chunk:
                continue
            ph = ",".join(["?"] * len(chunk))
            rows = conn.execute(
                f"""
                SELECT pr.prediction_id, pr.race_id, rs.horse_id
                FROM prediction_runs pr
                JOIN prediction_results rs ON rs.prediction_id = pr.prediction_id
                WHERE pr.prediction_id IN ({ph}) AND rs.rank <= 3
                """,
                chunk,
            ).fetchall()
            for r in rows:
                pid = str(r[0] or "")
                rid = str(r[1] or "")
                hid = str(r[2] or "")
                if pid and rid and hid and winner_by_race.get(rid) == hid:
                    out[pid] = 1
        conn.close()
        return out

    def fetch_canary_evaluation_top_predictions(
        self,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        target: str | None = None,
        canary_percent: int | None = None,
    ) -> list[dict[str, Any]]:
        model_ids = self.get_model_ids_by_target(target)
        where: list[str] = ["COALESCE(pr.router_mode, 'off') = 'canary'", "rs.rank = 1"]
        params: list[Any] = []
        if date_from:
            where.append("pr.race_date >= ?")
            params.append(str(date_from))
        if date_to:
            where.append("pr.race_date <= ?")
            params.append(str(date_to))
        if model_ids:
            ph = ",".join(["?"] * len(model_ids))
            where.append(f"pr.model_id IN ({ph})")
            params.extend(model_ids)
        if canary_percent is not None:
            where.append("COALESCE(pr.canary_percent, 0) = ?")
            params.append(int(canary_percent))

        conn = self._connect()
        rows = conn.execute(
            f"""
            SELECT pr.prediction_id, pr.race_id, pr.race_date,
                   pr.model_id, pr.actual_model_id,
                   pr.selected_model_id, pr.route_type,
                   pr.fallback_used,
                   pr.canary_percent, pr.canary_bucket, pr.canary_selected,
                   pr.effective_router_mode,
                   rs.horse_id, rs.odds, rs.expected_value
            FROM prediction_runs pr
            JOIN prediction_results rs ON rs.prediction_id = pr.prediction_id
            WHERE {' AND '.join(where)}
            ORDER BY pr.created_at DESC
            """,
            params,
        ).fetchall()
        conn.close()

        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "prediction_id": str(r[0] or ""),
                    "race_id": str(r[1] or ""),
                    "race_date": str(r[2] or ""),
                    "model_id": str(r[3] or ""),
                    "actual_model_id": str(r[4] or ""),
                    "selected_model_id": str(r[5] or ""),
                    "route_type": str(r[6] or ""),
                    "fallback_used": bool(int(r[7] or 0)),
                    "canary_percent": (int(r[8]) if r[8] is not None else None),
                    "canary_bucket": (int(r[9]) if r[9] is not None else None),
                    "canary_selected": bool(int(r[10] or 0)),
                    "effective_router_mode": str(r[11] or ""),
                    "horse_id": str(r[12] or ""),
                    "odds": (float(r[13]) if r[13] is not None else None),
                    "expected_value": (float(r[14]) if r[14] is not None else None),
                }
            )
        return out

    def record_experiment(
        self,
        *,
        experiment_id: str,
        target: str,
        model_type: str,
        git_hash: str,
        dataset_from: str,
        dataset_to: str,
        feature_store_version: str | None,
        feature_quality_score: float | None,
        params: dict[str, Any],
        metrics: dict[str, Any],
        artifacts: dict[str, Any],
        status: str = "completed",
    ) -> str:
        conn = self._connect()
        conn.execute(
            """
            INSERT OR REPLACE INTO experiment_runs (
                experiment_id, created_at, target, model_type, git_hash,
                dataset_from, dataset_to, feature_store_version, feature_quality_score,
                params_json, metrics_json, artifacts_json, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                experiment_id,
                _now(),
                target,
                model_type,
                git_hash,
                dataset_from,
                dataset_to,
                feature_store_version,
                feature_quality_score,
                json.dumps(params, ensure_ascii=False, default=str),
                json.dumps(metrics, ensure_ascii=False, default=str),
                json.dumps(artifacts, ensure_ascii=False, default=str),
                status,
            ),
        )
        conn.commit()
        conn.close()
        return experiment_id

    def register_model(
        self,
        *,
        model_id: str,
        target: str,
        experiment_id: str,
        dataset_from: str,
        dataset_to: str,
        feature_store_version: str | None,
        feature_quality_score: float | None,
        metrics: dict[str, Any],
        stage: str = "candidate",
        status: str = "active",
        notes: str | None = None,
    ) -> int:
        conn = self._connect()
        cur = conn.execute(
            """
            INSERT INTO model_registry (
                model_id, created_at, target, status, stage, experiment_id,
                dataset_from, dataset_to, feature_store_version, feature_quality_score,
                metrics_json, notes, promoted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                model_id,
                _now(),
                target,
                status,
                stage,
                experiment_id,
                dataset_from,
                dataset_to,
                feature_store_version,
                feature_quality_score,
                json.dumps(metrics, ensure_ascii=False, default=str),
                notes,
                None,
            ),
        )
        rid = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return rid

    def list_experiments(self, limit: int = 50) -> list[dict[str, Any]]:
        n = max(1, min(int(limit), 200))
        conn = self._connect()
        rows = conn.execute(
            """
            SELECT experiment_id, created_at, target, model_type, git_hash,
                   dataset_from, dataset_to, feature_store_version, feature_quality_score,
                   params_json, metrics_json, artifacts_json, status
            FROM experiment_runs
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (n,),
        ).fetchall()
        conn.close()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "experiment_id": str(r[0]),
                    "created_at": str(r[1]),
                    "target": str(r[2]),
                    "model_type": str(r[3]),
                    "git_hash": str(r[4] or ""),
                    "dataset_from": str(r[5] or ""),
                    "dataset_to": str(r[6] or ""),
                    "feature_store_version": str(r[7] or ""),
                    "feature_quality_score": (float(r[8]) if r[8] is not None else None),
                    "params": json.loads(r[9] or "{}"),
                    "metrics": json.loads(r[10] or "{}"),
                    "artifacts": json.loads(r[11] or "{}"),
                    "status": str(r[12]),
                }
            )
        return out

    def list_models(self, limit: int = 50) -> list[dict[str, Any]]:
        n = max(1, min(int(limit), 200))
        conn = self._connect()
        rows = conn.execute(
            """
            SELECT id, model_id, created_at, target, status, stage, experiment_id,
                   dataset_from, dataset_to, feature_store_version, feature_quality_score,
                   metrics_json, notes, promoted_at
            FROM model_registry
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (n,),
        ).fetchall()
        conn.close()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "id": int(r[0]),
                    "model_id": str(r[1]),
                    "created_at": str(r[2]),
                    "target": str(r[3]),
                    "status": str(r[4]),
                    "stage": str(r[5]),
                    "experiment_id": str(r[6] or ""),
                    "dataset_from": str(r[7] or ""),
                    "dataset_to": str(r[8] or ""),
                    "feature_store_version": str(r[9] or ""),
                    "feature_quality_score": (float(r[10]) if r[10] is not None else None),
                    "metrics": json.loads(r[11] or "{}"),
                    "notes": str(r[12] or ""),
                    "promoted_at": str(r[13] or ""),
                }
            )
        return out

    def promote_model(self, model_id: str, target: str, notes: str = "") -> int:
        conn = self._connect()
        conn.execute(
            "UPDATE model_registry SET stage = 'archived' WHERE target = ? AND stage = 'production'",
            (target,),
        )
        cur = conn.execute(
            """
            UPDATE model_registry
            SET stage = 'production', promoted_at = ?, notes = ?
            WHERE model_id = ?
            """,
            (_now(), notes, model_id),
        )
        changed = int(cur.rowcount)
        conn.commit()
        conn.close()
        return changed

    def record_prediction_run(
        self,
        *,
        prediction_id: str,
        race_id: str,
        race_date: str,
        model_id: str,
        experiment_id: str | None,
        feature_store_version: str | None,
        prediction_version: str,
        quality_gate: dict[str, Any],
        metadata: dict[str, Any],
        predictions: list[dict[str, Any]],
    ) -> str:
        scenario_meta = metadata.get("scenario") if isinstance(metadata, dict) else {}
        if not isinstance(scenario_meta, dict):
            scenario_meta = {}

        scenario_id = str(scenario_meta.get("scenario_id") or "")
        scenario_hash = str(scenario_meta.get("scenario_hash") or "")
        scenario_reason = str(scenario_meta.get("reason") or "")
        scenario_confidence = scenario_meta.get("confidence")
        winning_pattern = str(scenario_meta.get("winning_pattern") or "")
        expected_pace = str(scenario_meta.get("pace") or "")
        expected_bias = str(scenario_meta.get("bias") or "")
        routing_meta = metadata.get("routing") if isinstance(metadata, dict) else {}
        if not isinstance(routing_meta, dict):
            routing_meta = {}
        selected_model_id = str(routing_meta.get("selected_model_id") or model_id or "")
        route_type = str(routing_meta.get("route_type") or "")
        matched_scenario_key = str(routing_meta.get("matched_scenario_key") or "")
        matched_scenario_value = str(routing_meta.get("matched_scenario_value") or "")
        router_reason = str(routing_meta.get("router_reason") or "")
        fallback_used = 1 if bool(routing_meta.get("fallback_used")) else 0
        router_mode = str(routing_meta.get("router_mode") or "off")
        actual_model_id = str(routing_meta.get("actual_model_id") or model_id or "")
        shadow_selected_model_id = str(routing_meta.get("shadow_selected_model_id") or "")
        shadow_route_type = str(routing_meta.get("shadow_route_type") or "")
        shadow_matched_scenario_key = str(routing_meta.get("shadow_matched_scenario_key") or "")
        shadow_matched_scenario_value = str(routing_meta.get("shadow_matched_scenario_value") or "")
        shadow_router_reason = str(routing_meta.get("shadow_router_reason") or "")
        shadow_fallback_used = 1 if bool(routing_meta.get("shadow_fallback_used")) else 0
        canary_percent = routing_meta.get("canary_percent")
        canary_bucket = routing_meta.get("canary_bucket")
        canary_selected = 1 if bool(routing_meta.get("canary_selected")) else 0
        effective_router_mode = str(routing_meta.get("effective_router_mode") or router_mode or "off")

        conn = self._connect()
        conn.execute(
            """
            INSERT OR REPLACE INTO prediction_runs (
                prediction_id, created_at, race_id, race_date, model_id,
                experiment_id, feature_store_version, prediction_version,
                quality_gate_json, metadata_json,
                scenario_id, scenario_hash, scenario_features_json,
                scenario_reason, scenario_confidence,
                winning_pattern, expected_pace, expected_bias,
                selected_model_id, route_type,
                matched_scenario_key, matched_scenario_value,
                router_reason, fallback_used,
                router_mode, actual_model_id,
                shadow_selected_model_id, shadow_route_type,
                shadow_matched_scenario_key, shadow_matched_scenario_value,
                shadow_router_reason, shadow_fallback_used,
                canary_percent, canary_bucket, canary_selected, effective_router_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                prediction_id,
                _now(),
                race_id,
                race_date,
                model_id,
                experiment_id,
                feature_store_version,
                prediction_version,
                json.dumps(quality_gate or {}, ensure_ascii=False, default=str),
                json.dumps(metadata or {}, ensure_ascii=False, default=str),
                scenario_id,
                scenario_hash,
                json.dumps(scenario_meta.get("scenario_features") or {}, ensure_ascii=False, default=str),
                scenario_reason,
                (float(scenario_confidence) if scenario_confidence is not None else None),
                winning_pattern,
                expected_pace,
                expected_bias,
                selected_model_id,
                route_type,
                matched_scenario_key,
                matched_scenario_value,
                router_reason,
                fallback_used,
                router_mode,
                actual_model_id,
                shadow_selected_model_id,
                shadow_route_type,
                shadow_matched_scenario_key,
                shadow_matched_scenario_value,
                shadow_router_reason,
                shadow_fallback_used,
                (int(canary_percent) if canary_percent is not None else None),
                (int(canary_bucket) if canary_bucket is not None else None),
                canary_selected,
                effective_router_mode,
            ),
        )
        conn.execute("DELETE FROM prediction_results WHERE prediction_id = ?", (prediction_id,))
        rows = []
        for p in predictions:
            rows.append(
                (
                    prediction_id,
                    str(p.get("horse_id") or ""),
                    int(p.get("horse_number") or 0) if p.get("horse_number") is not None else None,
                    str(p.get("horse_name") or ""),
                    float(p.get("p_raw") or p.get("score") or 0.0),
                    float(p.get("p_norm") or p.get("win_probability") or p.get("probability") or 0.0),
                    float(p.get("p_ensemble") or p.get("calibrated_probability") or p.get("p_norm") or 0.0),
                    int(p.get("predicted_rank") or p.get("rank") or 0) if p.get("predicted_rank") is not None or p.get("rank") is not None else None,
                    float(p.get("expected_value")) if p.get("expected_value") is not None else None,
                    float(p.get("odds")) if p.get("odds") is not None else None,
                    1 if bool(p.get("buy_flag")) else 0,
                    str(p.get("scenario_reason") or p.get("reason") or ""),
                    float(p.get("scenario_confidence")) if p.get("scenario_confidence") is not None else (float(p.get("confidence")) if p.get("confidence") is not None else None),
                    float(p.get("scenario_fit")) if p.get("scenario_fit") is not None else None,
                )
            )
        conn.executemany(
            """
            INSERT INTO prediction_results (
                prediction_id, horse_id, horse_number, horse_name, score,
                probability, calibrated_probability, rank, expected_value, odds, buy_flag,
                reason, confidence, scenario_fit
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        conn.close()
        return prediction_id

    def list_predictions(self, limit: int = 50) -> list[dict[str, Any]]:
        n = max(1, min(int(limit), 300))
        conn = self._connect()
        rows = conn.execute(
            """
            SELECT prediction_id, created_at, race_id, race_date, model_id,
                   experiment_id, feature_store_version, prediction_version,
                 quality_gate_json, metadata_json,
                 scenario_id, scenario_hash, scenario_features_json,
                 scenario_reason, scenario_confidence,
                  winning_pattern, expected_pace, expected_bias,
                  selected_model_id, route_type,
                  matched_scenario_key, matched_scenario_value,
                router_reason, fallback_used,
                router_mode, actual_model_id,
                shadow_selected_model_id, shadow_route_type,
                shadow_matched_scenario_key, shadow_matched_scenario_value,
                shadow_router_reason, shadow_fallback_used,
                canary_percent, canary_bucket, canary_selected, effective_router_mode
            FROM prediction_runs
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (n,),
        ).fetchall()
        conn.close()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "prediction_id": str(r[0]),
                    "created_at": str(r[1]),
                    "race_id": str(r[2]),
                    "race_date": str(r[3] or ""),
                    "model_id": str(r[4]),
                    "experiment_id": str(r[5] or ""),
                    "feature_store_version": str(r[6] or ""),
                    "prediction_version": str(r[7] or ""),
                    "quality_gate": json.loads(r[8] or "{}"),
                    "metadata": json.loads(r[9] or "{}"),
                    "scenario": {
                        "scenario_id": str(r[10] or ""),
                        "scenario_hash": str(r[11] or ""),
                        "scenario_features": json.loads(r[12] or "{}"),
                        "reason": str(r[13] or ""),
                        "confidence": (float(r[14]) if r[14] is not None else None),
                        "winning_pattern": str(r[15] or ""),
                        "pace": str(r[16] or ""),
                        "bias": str(r[17] or ""),
                    },
                    "routing": {
                        "selected_model_id": str(r[18] or ""),
                        "route_type": str(r[19] or ""),
                        "matched_scenario_key": str(r[20] or ""),
                        "matched_scenario_value": str(r[21] or ""),
                        "router_reason": str(r[22] or ""),
                        "fallback_used": bool(int(r[23] or 0)),
                        "router_mode": str(r[24] or "off"),
                        "actual_model_id": str(r[25] or ""),
                        "shadow_selected_model_id": str(r[26] or ""),
                        "shadow_route_type": str(r[27] or ""),
                        "shadow_matched_scenario_key": str(r[28] or ""),
                        "shadow_matched_scenario_value": str(r[29] or ""),
                        "shadow_router_reason": str(r[30] or ""),
                        "shadow_fallback_used": bool(int(r[31] or 0)),
                        "canary_percent": (int(r[32]) if r[32] is not None else None),
                        "canary_bucket": (int(r[33]) if r[33] is not None else None),
                        "canary_selected": bool(int(r[34] or 0)),
                        "effective_router_mode": str(r[35] or "off"),
                    },
                }
            )
        return out

    def get_prediction(self, prediction_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        run = conn.execute(
            """
            SELECT prediction_id, created_at, race_id, race_date, model_id,
                   experiment_id, feature_store_version, prediction_version,
                 quality_gate_json, metadata_json,
                 scenario_id, scenario_hash, scenario_features_json,
                 scenario_reason, scenario_confidence,
                  winning_pattern, expected_pace, expected_bias,
                  selected_model_id, route_type,
                  matched_scenario_key, matched_scenario_value,
                router_reason, fallback_used,
                router_mode, actual_model_id,
                shadow_selected_model_id, shadow_route_type,
                shadow_matched_scenario_key, shadow_matched_scenario_value,
                shadow_router_reason, shadow_fallback_used,
                canary_percent, canary_bucket, canary_selected, effective_router_mode
            FROM prediction_runs
            WHERE prediction_id = ?
            """,
            (prediction_id,),
        ).fetchone()
        if not run:
            conn.close()
            return None
        rows = conn.execute(
            """
            SELECT horse_id, horse_number, horse_name, score, probability,
                 calibrated_probability, rank, expected_value, odds, buy_flag,
                 reason, confidence, scenario_fit
            FROM prediction_results
            WHERE prediction_id = ?
            ORDER BY rank ASC, probability DESC
            """,
            (prediction_id,),
        ).fetchall()
        conn.close()
        results: list[dict[str, Any]] = []
        for r in rows:
            results.append(
                {
                    "horse_id": str(r[0] or ""),
                    "horse_number": (int(r[1]) if r[1] is not None else None),
                    "horse_name": str(r[2] or ""),
                    "score": (float(r[3]) if r[3] is not None else None),
                    "probability": (float(r[4]) if r[4] is not None else None),
                    "calibrated_probability": (float(r[5]) if r[5] is not None else None),
                    "rank": (int(r[6]) if r[6] is not None else None),
                    "expected_value": (float(r[7]) if r[7] is not None else None),
                    "odds": (float(r[8]) if r[8] is not None else None),
                    "buy_flag": bool(int(r[9] or 0)),
                    "reason": str(r[10] or ""),
                    "confidence": (float(r[11]) if r[11] is not None else None),
                    "scenario_fit": (float(r[12]) if r[12] is not None else None),
                }
            )
        return {
            "prediction_id": str(run[0]),
            "created_at": str(run[1]),
            "race_id": str(run[2]),
            "race_date": str(run[3] or ""),
            "model_id": str(run[4]),
            "experiment_id": str(run[5] or ""),
            "feature_store_version": str(run[6] or ""),
            "prediction_version": str(run[7] or ""),
            "quality_gate": json.loads(run[8] or "{}"),
            "metadata": json.loads(run[9] or "{}"),
            "scenario": {
                "scenario_id": str(run[10] or ""),
                "scenario_hash": str(run[11] or ""),
                "scenario_features": json.loads(run[12] or "{}"),
                "reason": str(run[13] or ""),
                "confidence": (float(run[14]) if run[14] is not None else None),
                "winning_pattern": str(run[15] or ""),
                "pace": str(run[16] or ""),
                "bias": str(run[17] or ""),
            },
            "routing": {
                "selected_model_id": str(run[18] or ""),
                "route_type": str(run[19] or ""),
                "matched_scenario_key": str(run[20] or ""),
                "matched_scenario_value": str(run[21] or ""),
                "router_reason": str(run[22] or ""),
                "fallback_used": bool(int(run[23] or 0)),
                "router_mode": str(run[24] or "off"),
                "actual_model_id": str(run[25] or ""),
                "shadow_selected_model_id": str(run[26] or ""),
                "shadow_route_type": str(run[27] or ""),
                "shadow_matched_scenario_key": str(run[28] or ""),
                "shadow_matched_scenario_value": str(run[29] or ""),
                "shadow_router_reason": str(run[30] or ""),
                "shadow_fallback_used": bool(int(run[31] or 0)),
                "canary_percent": (int(run[32]) if run[32] is not None else None),
                "canary_bucket": (int(run[33]) if run[33] is not None else None),
                "canary_selected": bool(int(run[34] or 0)),
                "effective_router_mode": str(run[35] or "off"),
            },
            "results": results,
        }

    def record_bet(
        self,
        *,
        prediction_id: str | None,
        race_id: str,
        bet_type: str,
        combinations: list[str],
        unit_price: int,
        quantity: int,
        total_cost: int,
        expected_return: float | None,
        odds: dict[str, Any] | None,
        status: str = "planned",
        payout: float | None = None,
    ) -> int:
        conn = self._connect()
        cur = conn.execute(
            """
            INSERT INTO bet_registry (
                created_at, prediction_id, race_id, bet_type, combinations_json,
                unit_price, quantity, total_cost, expected_return, odds_json,
                status, payout
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _now(),
                prediction_id,
                race_id,
                bet_type,
                json.dumps(combinations or [], ensure_ascii=False),
                int(unit_price),
                int(quantity),
                int(total_cost),
                (float(expected_return) if expected_return is not None else None),
                json.dumps(odds or {}, ensure_ascii=False),
                status,
                (float(payout) if payout is not None else None),
            ),
        )
        rid = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return rid

    def evaluate_prediction(
        self,
        *,
        prediction_id: str,
        race_db_path: str,
    ) -> dict[str, Any] | None:
        pred = self.get_prediction(prediction_id)
        if not pred:
            return None

        race_id = str(pred.get("race_id") or "")
        model_id = str(pred.get("model_id") or "")
        pred_rows = pred.get("results") or []
        if not race_id or not pred_rows:
            return None

        conn_race = sqlite3.connect(race_db_path)
        rows = conn_race.execute(
            "SELECT data FROM race_results_ultimate WHERE race_id = ?",
            (race_id,),
        ).fetchall()
        conn_race.close()
        if not rows:
            return None

        actual_by_horse: dict[str, int] = {}
        for row in rows:
            try:
                d = json.loads(row[0] or "{}")
                hid = str(d.get("horse_id") or "")
                finish = d.get("finish") or d.get("finish_position")
                if hid and finish is not None:
                    actual_by_horse[hid] = int(finish)
            except Exception:
                continue

        if not actual_by_horse:
            return None

        top_pred = None
        for p in pred_rows:
            if p.get("rank") == 1:
                top_pred = p
                break
        top1_hit = 0
        if top_pred is not None:
            _hid = str(top_pred.get("horse_id") or "")
            top1_hit = 1 if actual_by_horse.get(_hid) == 1 else 0

        top3_horses = [str(p.get("horse_id") or "") for p in pred_rows if (p.get("rank") or 99) <= 3]
        top3_hit = 1 if any(actual_by_horse.get(h) == 1 for h in top3_horses if h) else 0

        mae_terms: list[float] = []
        for p in pred_rows:
            hid = str(p.get("horse_id") or "")
            prob = p.get("calibrated_probability")
            if hid in actual_by_horse and prob is not None:
                y = 1.0 if int(actual_by_horse[hid]) == 1 else 0.0
                mae_terms.append(abs(float(prob) - y))
        calibration_error = float(sum(mae_terms) / len(mae_terms)) if mae_terms else 0.0

        roi = 0.0
        hit_rate = float(top1_hit)
        metrics = {
            "top1_hit": int(top1_hit),
            "top3_hit": int(top3_hit),
            "calibration_error": float(calibration_error),
            "n_horses": int(len(pred_rows)),
            "n_actual": int(len(actual_by_horse)),
        }

        conn = self._connect()
        conn.execute(
            """
            INSERT INTO evaluation_registry (
                created_at, prediction_id, race_id, model_id,
                hit_rate, roi, calibration_error, top1_hit, top3_hit, metrics_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _now(),
                prediction_id,
                race_id,
                model_id,
                hit_rate,
                roi,
                calibration_error,
                top1_hit,
                top3_hit,
                json.dumps(metrics, ensure_ascii=False),
            ),
        )
        conn.commit()
        conn.close()

        return {
            "prediction_id": prediction_id,
            "race_id": race_id,
            "model_id": model_id,
            "hit_rate": hit_rate,
            "roi": roi,
            "calibration_error": calibration_error,
            "top1_hit": int(top1_hit),
            "top3_hit": int(top3_hit),
            "metrics": metrics,
        }

    def save_scenario_adoption_decisions(
        self,
        *,
        decisions: list[dict[str, Any]],
    ) -> int:
        if not decisions:
            return 0
        conn = self._connect()
        rows = []
        for d in decisions:
            rows.append(
                (
                    str(d.get("decision_id") or ""),
                    _now(),
                    str(d.get("experiment_id") or ""),
                    str(d.get("baseline_model_id") or ""),
                    str(d.get("challenger_model_id") or ""),
                    str(d.get("scenario_key") or ""),
                    str(d.get("scenario_value") or ""),
                    str(d.get("decision") or "REJECT"),
                    str(d.get("reason") or ""),
                    (float(d.get("roi_lift")) if d.get("roi_lift") is not None else None),
                    (float(d.get("hit_rate_lift")) if d.get("hit_rate_lift") is not None else None),
                    (float(d.get("top3_lift")) if d.get("top3_lift") is not None else None),
                    (float(d.get("ev_lift")) if d.get("ev_lift") is not None else None),
                    (float(d.get("p_value")) if d.get("p_value") is not None else None),
                    (float(d.get("p_value_fdr")) if d.get("p_value_fdr") is not None else None),
                    (float(d.get("ci_lower")) if d.get("ci_lower") is not None else None),
                    (float(d.get("ci_upper")) if d.get("ci_upper") is not None else None),
                    (int(d.get("overlap")) if d.get("overlap") is not None else None),
                    json.dumps(d.get("details") or {}, ensure_ascii=False, default=str),
                )
            )
        conn.executemany(
            """
            INSERT OR REPLACE INTO scenario_adoption_decisions (
                decision_id, created_at, experiment_id,
                baseline_model_id, challenger_model_id,
                scenario_key, scenario_value,
                decision, reason,
                roi_lift, hit_rate_lift, top3_lift, ev_lift,
                p_value, p_value_fdr,
                ci_lower, ci_upper,
                overlap, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        conn.close()
        return int(len(rows))

    def upsert_scenario_model_policies(
        self,
        *,
        policies: list[dict[str, Any]],
    ) -> int:
        if not policies:
            return 0
        conn = self._connect()
        rows = []
        for p in policies:
            rows.append(
                (
                    str(p.get("policy_id") or ""),
                    _now(),
                    _now(),
                    str(p.get("scenario_key") or ""),
                    str(p.get("scenario_value") or ""),
                    str(p.get("model_id") or ""),
                    str(p.get("feature_set_id") or ""),
                    str(p.get("strategy_id") or ""),
                    int(p.get("priority") or 100),
                    (float(p.get("confidence")) if p.get("confidence") is not None else None),
                    str(p.get("status") or "active"),
                    str(p.get("source_decision_id") or ""),
                    str(p.get("notes") or ""),
                )
            )
        conn.executemany(
            """
            INSERT OR REPLACE INTO scenario_model_policies (
                policy_id, created_at, updated_at,
                scenario_key, scenario_value,
                model_id, feature_set_id, strategy_id,
                priority, confidence, status, source_decision_id, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        conn.close()
        return int(len(rows))

    def list_scenario_model_policies(
        self,
        *,
        status: str | None = None,
        target: str | None = None,
    ) -> list[dict[str, Any]]:
        model_ids = self.get_model_ids_by_target(target) if target else []

        where: list[str] = []
        params: list[Any] = []
        if status:
            where.append("status = ?")
            params.append(str(status))
        if target:
            if not model_ids:
                return []
            ph = ",".join(["?"] * len(model_ids))
            where.append(f"model_id IN ({ph})")
            params.extend(model_ids)

        sql = """
            SELECT policy_id, created_at, updated_at,
                   scenario_key, scenario_value,
                   model_id, feature_set_id, strategy_id,
                   priority, confidence, status,
                   source_decision_id, notes
            FROM scenario_model_policies
        """
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY status ASC, priority DESC, updated_at DESC"

        conn = self._connect()
        rows = conn.execute(sql, params).fetchall()
        conn.close()

        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "policy_id": str(r[0] or ""),
                    "created_at": str(r[1] or ""),
                    "updated_at": str(r[2] or ""),
                    "scenario_key": str(r[3] or ""),
                    "scenario_value": str(r[4] or ""),
                    "model_id": str(r[5] or ""),
                    "feature_set_id": str(r[6] or ""),
                    "strategy_id": str(r[7] or ""),
                    "priority": int(r[8] or 0),
                    "confidence": (float(r[9]) if r[9] is not None else None),
                    "status": str(r[10] or ""),
                    "source_decision_id": str(r[11] or ""),
                    "notes": str(r[12] or ""),
                }
            )
        return out

    def get_scenario_model_policy_by_id(self, *, policy_id: str) -> dict[str, Any] | None:
        pid = str(policy_id or "").strip()
        if not pid:
            return None
        conn = self._connect()
        row = conn.execute(
            """
            SELECT policy_id, created_at, updated_at,
                   scenario_key, scenario_value,
                   model_id, feature_set_id, strategy_id,
                   priority, confidence, status,
                   source_decision_id, notes
            FROM scenario_model_policies
            WHERE policy_id = ?
            LIMIT 1
            """,
            (pid,),
        ).fetchone()
        conn.close()
        if not row:
            return None
        return {
            "policy_id": str(row[0] or ""),
            "created_at": str(row[1] or ""),
            "updated_at": str(row[2] or ""),
            "scenario_key": str(row[3] or ""),
            "scenario_value": str(row[4] or ""),
            "model_id": str(row[5] or ""),
            "feature_set_id": str(row[6] or ""),
            "strategy_id": str(row[7] or ""),
            "priority": int(row[8] or 0),
            "confidence": (float(row[9]) if row[9] is not None else None),
            "status": str(row[10] or ""),
            "source_decision_id": str(row[11] or ""),
            "notes": str(row[12] or ""),
        }

    def update_scenario_model_policy(
        self,
        *,
        policy_id: str,
        status: str | None = None,
        priority: int | None = None,
        note_suffix: str = "",
    ) -> dict[str, Any] | None:
        pid = str(policy_id or "").strip()
        if not pid:
            return None
        cur = self.get_scenario_model_policy_by_id(policy_id=pid)
        if not cur:
            return None
        next_status = str(status if status is not None else cur.get("status") or "")
        next_priority = int(priority if priority is not None else int(cur.get("priority") or 100))
        cur_notes = str(cur.get("notes") or "")
        next_notes = cur_notes
        if note_suffix:
            next_notes = (cur_notes + " | " + str(note_suffix)).strip(" |")

        conn = self._connect()
        conn.execute(
            """
            UPDATE scenario_model_policies
            SET status = ?, priority = ?, notes = ?, updated_at = ?
            WHERE policy_id = ?
            """,
            (next_status, next_priority, next_notes, _now(), pid),
        )
        conn.commit()
        conn.close()
        return self.get_scenario_model_policy_by_id(policy_id=pid)

    def save_scenario_policy_evaluations(
        self,
        *,
        evaluations: list[dict[str, Any]],
    ) -> int:
        if not evaluations:
            return 0
        rows = []
        for e in evaluations:
            rows.append(
                (
                    str(e.get("evaluation_id") or ""),
                    _now(),
                    str(e.get("policy_id") or ""),
                    str(e.get("scenario_key") or ""),
                    str(e.get("scenario_value") or ""),
                    str(e.get("model_id") or ""),
                    (int(e.get("races")) if e.get("races") is not None else None),
                    (float(e.get("router_roi")) if e.get("router_roi") is not None else None),
                    (float(e.get("global_roi")) if e.get("global_roi") is not None else None),
                    (float(e.get("roi_lift")) if e.get("roi_lift") is not None else None),
                    (float(e.get("hit_rate_lift")) if e.get("hit_rate_lift") is not None else None),
                    str(e.get("action") or "WATCH"),
                    str(e.get("reason") or ""),
                    json.dumps(e.get("details") or {}, ensure_ascii=False, default=str),
                )
            )

        conn = self._connect()
        conn.executemany(
            """
            INSERT OR REPLACE INTO scenario_policy_evaluations (
                evaluation_id, created_at,
                policy_id, scenario_key, scenario_value, model_id,
                races, router_roi, global_roi, roi_lift, hit_rate_lift,
                action, reason, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        conn.close()
        return int(len(rows))

    def apply_scenario_policy_actions(
        self,
        *,
        actions: list[dict[str, Any]],
        priority_step: int,
    ) -> int:
        if not actions:
            return 0

        step = max(1, int(priority_step))
        conn = self._connect()
        changed = 0
        for a in actions:
            action = str(a.get("action") or "").upper()
            policy_id = str(a.get("policy_id") or "")
            if not policy_id:
                continue

            row = conn.execute(
                "SELECT priority, status FROM scenario_model_policies WHERE policy_id = ?",
                (policy_id,),
            ).fetchone()
            if not row:
                continue

            cur_priority = int(row[0] or 0)
            cur_status = str(row[1] or "")
            next_priority = cur_priority
            next_status = cur_status

            if action == "RAISE_PRIORITY":
                next_priority = min(1000, cur_priority + step)
            elif action == "LOWER_PRIORITY":
                next_priority = max(0, cur_priority - step)
            elif action == "DISABLE":
                next_status = "disabled"

            if next_priority != cur_priority or next_status != cur_status:
                conn.execute(
                    """
                    UPDATE scenario_model_policies
                    SET priority = ?, status = ?, updated_at = ?
                    WHERE policy_id = ?
                    """,
                    (next_priority, next_status, _now(), policy_id),
                )
                changed += 1

        conn.commit()
        conn.close()
        return int(changed)

    def list_recent_policy_evaluations(
        self,
        *,
        target: str | None = None,
        lookback_evaluations: int = 5,
        policy_ids: list[str] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        n = max(1, min(int(lookback_evaluations), 100))
        mids = self.get_model_ids_by_target(target) if target else []

        where: list[str] = []
        params: list[Any] = []
        if target:
            if not mids:
                return {}
            ph = ",".join(["?"] * len(mids))
            where.append(f"spe.model_id IN ({ph})")
            params.extend(mids)
        if policy_ids:
            ids = [str(x) for x in policy_ids if str(x)]
            if not ids:
                return {}
            ph = ",".join(["?"] * len(ids))
            where.append(f"spe.policy_id IN ({ph})")
            params.extend(ids)

        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        conn = self._connect()
        rows = conn.execute(
            f"""
            WITH ranked AS (
                SELECT spe.evaluation_id, spe.created_at,
                       spe.policy_id, spe.scenario_key, spe.scenario_value,
                       spe.model_id, spe.races,
                       spe.router_roi, spe.global_roi,
                       spe.roi_lift, spe.hit_rate_lift,
                       spe.action, spe.reason, spe.details_json,
                       ROW_NUMBER() OVER (
                           PARTITION BY spe.policy_id
                           ORDER BY spe.created_at DESC
                       ) AS rn
                FROM scenario_policy_evaluations spe
                {where_sql}
            )
            SELECT evaluation_id, created_at,
                   policy_id, scenario_key, scenario_value,
                   model_id, races,
                   router_roi, global_roi,
                   roi_lift, hit_rate_lift,
                   action, reason, details_json
            FROM ranked
            WHERE rn <= ?
            ORDER BY policy_id ASC, created_at DESC
            """,
            [*params, n],
        ).fetchall()
        conn.close()

        out: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            pid = str(r[2] or "")
            if not pid:
                continue
            out.setdefault(pid, []).append(
                {
                    "evaluation_id": str(r[0] or ""),
                    "created_at": str(r[1] or ""),
                    "policy_id": pid,
                    "scenario_key": str(r[3] or ""),
                    "scenario_value": str(r[4] or ""),
                    "model_id": str(r[5] or ""),
                    "races": (int(r[6]) if r[6] is not None else None),
                    "router_roi": (float(r[7]) if r[7] is not None else None),
                    "global_roi": (float(r[8]) if r[8] is not None else None),
                    "roi_lift": (float(r[9]) if r[9] is not None else None),
                    "hit_rate_lift": (float(r[10]) if r[10] is not None else None),
                    "action": str(r[11] or ""),
                    "reason": str(r[12] or ""),
                    "details": json.loads(r[13] or "{}"),
                }
            )
        return out

    def get_policy_lifecycle_states(self, *, policy_ids: list[str]) -> dict[str, dict[str, Any]]:
        ids = [str(x) for x in (policy_ids or []) if str(x)]
        if not ids:
            return {}
        ph = ",".join(["?"] * len(ids))
        conn = self._connect()
        rows = conn.execute(
            f"""
            SELECT policy_id, last_action,
                   consecutive_keep, consecutive_raise, consecutive_lower,
                   consecutive_disable, consecutive_watch, consecutive_needs_more_data,
                   cooldown_until, lifecycle_status, updated_at
            FROM scenario_policy_lifecycle
            WHERE policy_id IN ({ph})
            """,
            ids,
        ).fetchall()
        conn.close()

        out: dict[str, dict[str, Any]] = {}
        for r in rows:
            out[str(r[0] or "")] = {
                "policy_id": str(r[0] or ""),
                "last_action": str(r[1] or ""),
                "consecutive_keep": int(r[2] or 0),
                "consecutive_raise": int(r[3] or 0),
                "consecutive_lower": int(r[4] or 0),
                "consecutive_disable": int(r[5] or 0),
                "consecutive_watch": int(r[6] or 0),
                "consecutive_needs_more_data": int(r[7] or 0),
                "cooldown_until": str(r[8] or ""),
                "lifecycle_status": str(r[9] or ""),
                "updated_at": str(r[10] or ""),
            }
        return out

    def upsert_policy_lifecycle_states(self, *, states: list[dict[str, Any]]) -> int:
        if not states:
            return 0
        rows = []
        for s in states:
            rows.append(
                (
                    str(s.get("policy_id") or ""),
                    str(s.get("last_action") or ""),
                    int(s.get("consecutive_keep") or 0),
                    int(s.get("consecutive_raise") or 0),
                    int(s.get("consecutive_lower") or 0),
                    int(s.get("consecutive_disable") or 0),
                    int(s.get("consecutive_watch") or 0),
                    int(s.get("consecutive_needs_more_data") or 0),
                    str(s.get("cooldown_until") or ""),
                    str(s.get("lifecycle_status") or ""),
                    _now(),
                )
            )
        conn = self._connect()
        conn.executemany(
            """
            INSERT OR REPLACE INTO scenario_policy_lifecycle (
                policy_id, last_action,
                consecutive_keep, consecutive_raise, consecutive_lower,
                consecutive_disable, consecutive_watch, consecutive_needs_more_data,
                cooldown_until, lifecycle_status, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        conn.close()
        return int(len(rows))

    def apply_lifecycle_policy_actions(
        self,
        *,
        actions: list[dict[str, Any]],
        priority_step: int,
    ) -> int:
        if not actions:
            return 0
        step = max(1, int(priority_step))
        conn = self._connect()
        changed = 0
        for a in actions:
            action = str(a.get("lifecycle_action") or "").upper()
            policy_id = str(a.get("policy_id") or "")
            if not policy_id:
                continue
            row = conn.execute(
                "SELECT priority, status FROM scenario_model_policies WHERE policy_id = ?",
                (policy_id,),
            ).fetchone()
            if not row:
                continue

            cur_priority = int(row[0] or 0)
            cur_status = str(row[1] or "")
            next_priority = cur_priority
            next_status = cur_status

            if action == "CONFIRM_RAISE_PRIORITY":
                next_priority = min(1000, cur_priority + step)
            elif action == "CONFIRM_LOWER_PRIORITY":
                next_priority = max(0, cur_priority - step)
            elif action == "CONFIRM_DISABLE":
                next_status = "disabled"

            if next_priority != cur_priority or next_status != cur_status:
                conn.execute(
                    """
                    UPDATE scenario_model_policies
                    SET priority = ?, status = ?, updated_at = ?
                    WHERE policy_id = ?
                    """,
                    (next_priority, next_status, _now(), policy_id),
                )
                changed += 1

        conn.commit()
        conn.close()
        return int(changed)

    def get_router_rollout(
        self,
        *,
        target: str | None = None,
        create_if_missing: bool = False,
    ) -> dict[str, Any] | None:
        tgt = str(target or "win").strip() or "win"
        conn = self._connect()
        row = conn.execute(
            """
            SELECT rollout_id, target,
                   current_percent, previous_percent,
                   router_mode, status,
                   last_decision, last_reason,
                   started_at, updated_at
            FROM scenario_router_rollouts
            WHERE target = ?
            LIMIT 1
            """,
            (tgt,),
        ).fetchone()

        if not row and bool(create_if_missing):
            rid = f"srr_{tgt}_{uuid.uuid4().hex[:10]}"
            now = _now()
            try:
                conn.execute(
                    """
                    INSERT INTO scenario_router_rollouts (
                        rollout_id, target,
                        current_percent, previous_percent,
                        router_mode, status,
                        last_decision, last_reason,
                        started_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        rid,
                        tgt,
                        0,
                        0,
                        "shadow",
                        "SHADOW_ONLY",
                        "INIT",
                        "initial rollout state",
                        now,
                        now,
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                pass

            row = conn.execute(
                """
                SELECT rollout_id, target,
                       current_percent, previous_percent,
                       router_mode, status,
                       last_decision, last_reason,
                       started_at, updated_at
                FROM scenario_router_rollouts
                WHERE target = ?
                LIMIT 1
                """,
                (tgt,),
            ).fetchone()

        conn.close()
        if not row:
            return None

        return {
            "rollout_id": str(row[0] or ""),
            "target": str(row[1] or ""),
            "current_percent": int(row[2] or 0),
            "previous_percent": int(row[3] or 0),
            "router_mode": str(row[4] or ""),
            "status": str(row[5] or ""),
            "last_decision": str(row[6] or ""),
            "last_reason": str(row[7] or ""),
            "started_at": str(row[8] or ""),
            "updated_at": str(row[9] or ""),
        }

    def update_router_rollout(
        self,
        *,
        rollout_id: str,
        target: str,
        current_percent: int,
        previous_percent: int,
        router_mode: str,
        status: str,
        last_decision: str,
        last_reason: str,
    ) -> dict[str, Any] | None:
        rid = str(rollout_id or "").strip()
        tgt = str(target or "win").strip() or "win"
        if not rid:
            return None
        conn = self._connect()
        conn.execute(
            """
            UPDATE scenario_router_rollouts
            SET target = ?,
                current_percent = ?,
                previous_percent = ?,
                router_mode = ?,
                status = ?,
                last_decision = ?,
                last_reason = ?,
                updated_at = ?
            WHERE rollout_id = ?
            """,
            (
                tgt,
                int(current_percent),
                int(previous_percent),
                str(router_mode or "shadow"),
                str(status or "SHADOW_ONLY"),
                str(last_decision or ""),
                str(last_reason or ""),
                _now(),
                rid,
            ),
        )
        conn.commit()
        conn.close()
        return self.get_router_rollout(target=tgt, create_if_missing=False)

    def insert_router_rollout_event(
        self,
        *,
        rollout_id: str,
        target: str,
        from_percent: int,
        to_percent: int,
        decision: str,
        action: str,
        reason: str,
        summary: dict[str, Any] | None,
    ) -> str:
        rid = str(rollout_id or "").strip()
        tgt = str(target or "win").strip() or "win"
        eid = f"srre_{uuid.uuid4().hex[:12]}"
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO scenario_router_rollout_events (
                event_id, rollout_id, target,
                from_percent, to_percent,
                decision, action, reason,
                summary_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                eid,
                rid,
                tgt,
                int(from_percent),
                int(to_percent),
                str(decision or ""),
                str(action or ""),
                str(reason or ""),
                json.dumps(summary or {}, ensure_ascii=False, default=str),
                _now(),
            ),
        )
        conn.commit()
        conn.close()
        return eid

    def list_router_rollout_events(
        self,
        *,
        target: str | None = None,
        rollout_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        n = max(1, min(int(limit), 500))
        where: list[str] = []
        params: list[Any] = []
        if target:
            where.append("target = ?")
            params.append(str(target))
        if rollout_id:
            where.append("rollout_id = ?")
            params.append(str(rollout_id))
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""

        conn = self._connect()
        rows = conn.execute(
            f"""
            SELECT event_id, rollout_id, target,
                   from_percent, to_percent,
                   decision, action, reason,
                   summary_json, created_at
            FROM scenario_router_rollout_events
            {where_sql}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            [*params, n],
        ).fetchall()
        conn.close()

        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "event_id": str(r[0] or ""),
                    "rollout_id": str(r[1] or ""),
                    "target": str(r[2] or ""),
                    "from_percent": int(r[3] or 0),
                    "to_percent": int(r[4] or 0),
                    "decision": str(r[5] or ""),
                    "action": str(r[6] or ""),
                    "reason": str(r[7] or ""),
                    "summary": json.loads(r[8] or "{}"),
                    "created_at": str(r[9] or ""),
                }
            )
        return out

    def insert_router_rollout_run(
        self,
        *,
        target: str,
        date_from: str | None,
        date_to: str | None,
        decision: str,
        action: str,
        from_percent: int,
        to_percent: int,
        apply_updates: bool,
        status: str,
        error_message: str | None,
        summary: dict[str, Any] | None,
    ) -> str:
        rid = f"srr_run_{uuid.uuid4().hex[:12]}"
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO scenario_router_rollout_runs (
                run_id, target,
                date_from, date_to,
                decision, action,
                from_percent, to_percent,
                apply_updates, status,
                error_message, summary_json,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rid,
                str(target or "win"),
                (str(date_from) if date_from else None),
                (str(date_to) if date_to else None),
                str(decision or ""),
                str(action or ""),
                int(from_percent),
                int(to_percent),
                1 if bool(apply_updates) else 0,
                str(status or "SUCCESS"),
                (str(error_message) if error_message else None),
                json.dumps(summary or {}, ensure_ascii=False, default=str),
                _now(),
            ),
        )
        conn.commit()
        conn.close()
        return rid

    def list_router_rollout_runs(
        self,
        *,
        target: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        n = max(1, min(int(limit), 500))
        where_sql = ""
        params: list[Any] = []
        if target:
            where_sql = " WHERE target = ?"
            params.append(str(target))
        conn = self._connect()
        rows = conn.execute(
            f"""
            SELECT run_id, target,
                   date_from, date_to,
                   decision, action,
                   from_percent, to_percent,
                   apply_updates, status,
                   error_message, summary_json,
                   created_at
            FROM scenario_router_rollout_runs
            {where_sql}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            [*params, n],
        ).fetchall()
        conn.close()

        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "run_id": str(r[0] or ""),
                    "target": str(r[1] or ""),
                    "date_from": str(r[2] or ""),
                    "date_to": str(r[3] or ""),
                    "decision": str(r[4] or ""),
                    "action": str(r[5] or ""),
                    "from_percent": int(r[6] or 0),
                    "to_percent": int(r[7] or 0),
                    "apply_updates": bool(int(r[8] or 0)),
                    "status": str(r[9] or ""),
                    "error_message": str(r[10] or ""),
                    "summary": json.loads(r[11] or "{}"),
                    "created_at": str(r[12] or ""),
                }
            )
        return out

    def get_open_router_alert(self, *, target: str, alert_type: str) -> dict[str, Any] | None:
        conn = self._connect()
        row = conn.execute(
            """
            SELECT alert_id, target, severity, alert_type, status,
                   title, message, source_run_id, decision, action,
                   summary_json, created_at, resolved_at
            FROM scenario_router_alerts
            WHERE target = ? AND alert_type = ? AND status = 'open'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (str(target), str(alert_type)),
        ).fetchone()
        conn.close()
        if not row:
            return None
        return {
            "alert_id": str(row[0] or ""),
            "target": str(row[1] or ""),
            "severity": str(row[2] or ""),
            "alert_type": str(row[3] or ""),
            "status": str(row[4] or ""),
            "title": str(row[5] or ""),
            "message": str(row[6] or ""),
            "source_run_id": str(row[7] or ""),
            "decision": str(row[8] or ""),
            "action": str(row[9] or ""),
            "summary": json.loads(row[10] or "{}"),
            "created_at": str(row[11] or ""),
            "resolved_at": str(row[12] or ""),
        }

    def get_router_alert_by_id(self, *, alert_id: str) -> dict[str, Any] | None:
        aid = str(alert_id or "").strip()
        if not aid:
            return None
        conn = self._connect()
        row = conn.execute(
            """
            SELECT alert_id, target, severity, alert_type, status,
                   title, message, source_run_id, decision, action,
                   summary_json, created_at, resolved_at
            FROM scenario_router_alerts
            WHERE alert_id = ?
            LIMIT 1
            """,
            (aid,),
        ).fetchone()
        conn.close()
        if not row:
            return None
        return {
            "alert_id": str(row[0] or ""),
            "target": str(row[1] or ""),
            "severity": str(row[2] or ""),
            "alert_type": str(row[3] or ""),
            "status": str(row[4] or ""),
            "title": str(row[5] or ""),
            "message": str(row[6] or ""),
            "source_run_id": str(row[7] or ""),
            "decision": str(row[8] or ""),
            "action": str(row[9] or ""),
            "summary": json.loads(row[10] or "{}"),
            "created_at": str(row[11] or ""),
            "resolved_at": str(row[12] or ""),
        }

    def create_router_alert(
        self,
        *,
        target: str,
        severity: str,
        alert_type: str,
        title: str,
        message: str,
        source_run_id: str,
        decision: str,
        action: str,
        summary: dict[str, Any] | None,
    ) -> str:
        aid = f"sra_{uuid.uuid4().hex[:12]}"
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO scenario_router_alerts (
                alert_id, target, severity, alert_type,
                status, title, message,
                source_run_id, decision, action,
                summary_json, created_at, resolved_at
            ) VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                aid,
                str(target),
                str(severity),
                str(alert_type),
                str(title),
                str(message),
                str(source_run_id),
                str(decision or ""),
                str(action or ""),
                json.dumps(summary or {}, ensure_ascii=False, default=str),
                _now(),
            ),
        )
        conn.commit()
        conn.close()
        return aid

    def add_router_alert_event(
        self,
        *,
        alert_id: str,
        event_type: str,
        message: str,
        payload: dict[str, Any] | None,
    ) -> str:
        eid = f"srae_{uuid.uuid4().hex[:12]}"
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO scenario_router_alert_events (
                event_id, alert_id, event_type,
                message, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                eid,
                str(alert_id),
                str(event_type),
                str(message or ""),
                json.dumps(payload or {}, ensure_ascii=False, default=str),
                _now(),
            ),
        )
        conn.commit()
        conn.close()
        return eid

    def list_router_alerts(
        self,
        *,
        target: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        n = max(1, min(int(limit), 500))
        where: list[str] = []
        params: list[Any] = []
        if target:
            where.append("target = ?")
            params.append(str(target))
        if status:
            where.append("status = ?")
            params.append(str(status))
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""

        conn = self._connect()
        rows = conn.execute(
            f"""
            SELECT alert_id, target, severity, alert_type,
                   status, title, message,
                   source_run_id, decision, action,
                   summary_json, created_at, resolved_at
            FROM scenario_router_alerts
            {where_sql}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            [*params, n],
        ).fetchall()
        conn.close()

        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "alert_id": str(r[0] or ""),
                    "target": str(r[1] or ""),
                    "severity": str(r[2] or ""),
                    "alert_type": str(r[3] or ""),
                    "status": str(r[4] or ""),
                    "title": str(r[5] or ""),
                    "message": str(r[6] or ""),
                    "source_run_id": str(r[7] or ""),
                    "decision": str(r[8] or ""),
                    "action": str(r[9] or ""),
                    "summary": json.loads(r[10] or "{}"),
                    "created_at": str(r[11] or ""),
                    "resolved_at": str(r[12] or ""),
                }
            )
        return out

    def resolve_router_alert(self, *, alert_id: str, message: str = "") -> bool:
        aid = str(alert_id or "").strip()
        if not aid:
            return False
        conn = self._connect()
        cur = conn.execute(
            """
            UPDATE scenario_router_alerts
            SET status = 'resolved', resolved_at = ?
            WHERE alert_id = ? AND status <> 'resolved'
            """,
            (_now(), aid),
        )
        changed = int(cur.rowcount)
        conn.commit()
        conn.close()
        if changed > 0:
            self.add_router_alert_event(
                alert_id=aid,
                event_type="RESOLVED",
                message=(str(message or "alert resolved")),
                payload={},
            )
        return changed > 0

    def list_router_alert_events(
        self,
        *,
        alert_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        aid = str(alert_id or "").strip()
        if not aid:
            return []
        n = max(1, min(int(limit), 500))
        conn = self._connect()
        rows = conn.execute(
            """
            SELECT event_id, alert_id, event_type,
                   message, payload_json, created_at
            FROM scenario_router_alert_events
            WHERE alert_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (aid, n),
        ).fetchall()
        conn.close()

        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "event_id": str(r[0] or ""),
                    "alert_id": str(r[1] or ""),
                    "event_type": str(r[2] or ""),
                    "message": str(r[3] or ""),
                    "payload": json.loads(r[4] or "{}"),
                    "created_at": str(r[5] or ""),
                }
            )
        return out

    def upsert_router_notification_channel(
        self,
        *,
        channel_id: str | None,
        channel_type: str,
        name: str,
        enabled: bool,
        config: dict[str, Any] | None,
        severity_filter: str,
    ) -> str:
        cid = str(channel_id or "").strip() or f"srnc_{uuid.uuid4().hex[:12]}"
        now = _now()
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO scenario_router_notification_channels (
                channel_id, channel_type, name,
                enabled, config_json, severity_filter,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(channel_id) DO UPDATE SET
                channel_type = excluded.channel_type,
                name = excluded.name,
                enabled = excluded.enabled,
                config_json = excluded.config_json,
                severity_filter = excluded.severity_filter,
                updated_at = excluded.updated_at
            """,
            (
                cid,
                str(channel_type or "webhook"),
                str(name or cid),
                1 if bool(enabled) else 0,
                json.dumps(config or {}, ensure_ascii=False, default=str),
                str(severity_filter or "WARNING,CRITICAL"),
                now,
                now,
            ),
        )
        conn.commit()
        conn.close()
        return cid

    def list_router_notification_channels(
        self,
        *,
        enabled_only: bool = False,
        channel_types: list[str] | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        n = max(1, min(int(limit), 500))
        where: list[str] = []
        params: list[Any] = []
        if bool(enabled_only):
            where.append("enabled = 1")
        if channel_types:
            types = [str(x) for x in channel_types if str(x)]
            if types:
                ph = ",".join(["?"] * len(types))
                where.append(f"channel_type IN ({ph})")
                params.extend(types)
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""

        conn = self._connect()
        rows = conn.execute(
            f"""
            SELECT channel_id, channel_type, name,
                   enabled, config_json, severity_filter,
                   created_at, updated_at
            FROM scenario_router_notification_channels
            {where_sql}
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            [*params, n],
        ).fetchall()
        conn.close()

        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "channel_id": str(r[0] or ""),
                    "channel_type": str(r[1] or ""),
                    "name": str(r[2] or ""),
                    "enabled": bool(int(r[3] or 0)),
                    "config": json.loads(r[4] or "{}"),
                    "severity_filter": str(r[5] or ""),
                    "created_at": str(r[6] or ""),
                    "updated_at": str(r[7] or ""),
                }
            )
        return out

    def has_sent_notification_delivery(self, *, alert_id: str, channel_id: str) -> bool:
        conn = self._connect()
        row = conn.execute(
            """
            SELECT 1
            FROM scenario_router_notification_deliveries
            WHERE alert_id = ? AND channel_id = ? AND status = 'sent'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (str(alert_id), str(channel_id)),
        ).fetchone()
        conn.close()
        return bool(row)

    def insert_router_notification_delivery(
        self,
        *,
        alert_id: str,
        channel_id: str,
        status: str,
        attempt_count: int = 1,
        last_error: str | None = None,
        payload: dict[str, Any] | None = None,
        sent: bool = False,
    ) -> str:
        did = f"srnd_{uuid.uuid4().hex[:12]}"
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO scenario_router_notification_deliveries (
                delivery_id, alert_id, channel_id,
                status, attempt_count, last_error,
                payload_json, sent_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                did,
                str(alert_id),
                str(channel_id),
                str(status),
                max(1, int(attempt_count)),
                (str(last_error) if last_error else None),
                json.dumps(payload or {}, ensure_ascii=False, default=str),
                (_now() if bool(sent) else None),
                _now(),
            ),
        )
        conn.commit()
        conn.close()
        return did

    def list_router_notification_deliveries(
        self,
        *,
        target: str | None = None,
        alert_id: str | None = None,
        channel_id: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        n = max(1, min(int(limit), 500))
        where: list[str] = []
        params: list[Any] = []
        if alert_id:
            where.append("d.alert_id = ?")
            params.append(str(alert_id))
        if channel_id:
            where.append("d.channel_id = ?")
            params.append(str(channel_id))
        if status:
            where.append("d.status = ?")
            params.append(str(status))
        if target:
            where.append("a.target = ?")
            params.append(str(target))
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""

        conn = self._connect()
        rows = conn.execute(
            f"""
            SELECT d.delivery_id, d.alert_id, d.channel_id,
                   d.status, d.attempt_count, d.last_error,
                   d.payload_json, d.sent_at, d.created_at,
                   a.target
            FROM scenario_router_notification_deliveries d
            LEFT JOIN scenario_router_alerts a ON a.alert_id = d.alert_id
            {where_sql}
            ORDER BY d.created_at DESC
            LIMIT ?
            """,
            [*params, n],
        ).fetchall()
        conn.close()

        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "delivery_id": str(r[0] or ""),
                    "alert_id": str(r[1] or ""),
                    "channel_id": str(r[2] or ""),
                    "status": str(r[3] or ""),
                    "attempt_count": int(r[4] or 0),
                    "last_error": str(r[5] or ""),
                    "payload": json.loads(r[6] or "{}"),
                    "sent_at": str(r[7] or ""),
                    "created_at": str(r[8] or ""),
                    "target": str(r[9] or ""),
                }
            )
        return out

    def get_router_notification_delivery_by_id(self, *, delivery_id: str) -> dict[str, Any] | None:
        did = str(delivery_id or "").strip()
        if not did:
            return None

        conn = self._connect()
        row = conn.execute(
            """
            SELECT d.delivery_id, d.alert_id, d.channel_id,
                   d.status, d.attempt_count, d.last_error,
                   d.payload_json, d.sent_at, d.created_at,
                   a.target
            FROM scenario_router_notification_deliveries d
            LEFT JOIN scenario_router_alerts a ON a.alert_id = d.alert_id
            WHERE d.delivery_id = ?
            LIMIT 1
            """,
            (did,),
        ).fetchone()
        conn.close()
        if not row:
            return None
        return {
            "delivery_id": str(row[0] or ""),
            "alert_id": str(row[1] or ""),
            "channel_id": str(row[2] or ""),
            "status": str(row[3] or ""),
            "attempt_count": int(row[4] or 0),
            "last_error": str(row[5] or ""),
            "payload": json.loads(row[6] or "{}"),
            "sent_at": str(row[7] or ""),
            "created_at": str(row[8] or ""),
            "target": str(row[9] or ""),
        }

    def insert_router_runbook(
        self,
        *,
        alert_id: str,
        target: str,
        severity: str,
        alert_type: str,
        title: str,
        summary: str,
        root_cause_hypotheses: list[str] | None,
        checklist: list[str] | None,
        recommended_actions: list[str] | None,
        related_apis: list[str] | None,
        recovery_conditions: list[str] | None,
        observed_metrics: dict[str, Any] | None,
        threshold_comparison: list[dict[str, Any]] | None,
    ) -> str:
        rid = f"srrb_{uuid.uuid4().hex[:12]}"
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO scenario_router_runbooks (
                runbook_id, alert_id, target, severity, alert_type,
                title, summary,
                root_cause_hypotheses_json, checklist_json,
                recommended_actions_json, related_apis_json,
                recovery_conditions_json,
                observed_metrics_json, threshold_comparison_json,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rid,
                str(alert_id),
                str(target),
                str(severity),
                str(alert_type),
                str(title or ""),
                str(summary or ""),
                json.dumps(root_cause_hypotheses or [], ensure_ascii=False, default=str),
                json.dumps(checklist or [], ensure_ascii=False, default=str),
                json.dumps(recommended_actions or [], ensure_ascii=False, default=str),
                json.dumps(related_apis or [], ensure_ascii=False, default=str),
                json.dumps(recovery_conditions or [], ensure_ascii=False, default=str),
                json.dumps(observed_metrics or {}, ensure_ascii=False, default=str),
                json.dumps(threshold_comparison or [], ensure_ascii=False, default=str),
                _now(),
            ),
        )
        conn.commit()
        conn.close()
        return rid

    def get_router_runbook_by_id(self, *, runbook_id: str) -> dict[str, Any] | None:
        rid = str(runbook_id or "").strip()
        if not rid:
            return None
        conn = self._connect()
        row = conn.execute(
            """
            SELECT runbook_id, alert_id, target, severity, alert_type,
                   title, summary,
                   root_cause_hypotheses_json, checklist_json,
                   recommended_actions_json, related_apis_json,
                     recovery_conditions_json,
                     observed_metrics_json, threshold_comparison_json,
                     created_at
            FROM scenario_router_runbooks
            WHERE runbook_id = ?
            LIMIT 1
            """,
            (rid,),
        ).fetchone()
        conn.close()
        if not row:
            return None
        return {
            "runbook_id": str(row[0] or ""),
            "alert_id": str(row[1] or ""),
            "target": str(row[2] or ""),
            "severity": str(row[3] or ""),
            "alert_type": str(row[4] or ""),
            "title": str(row[5] or ""),
            "summary": str(row[6] or ""),
            "root_cause_hypotheses": json.loads(row[7] or "[]"),
            "checklist": json.loads(row[8] or "[]"),
            "recommended_actions": json.loads(row[9] or "[]"),
            "related_apis": json.loads(row[10] or "[]"),
            "recovery_conditions": json.loads(row[11] or "[]"),
            "observed_metrics": json.loads(row[12] or "{}"),
            "threshold_comparison": json.loads(row[13] or "[]"),
            "created_at": str(row[14] or ""),
        }

    def list_router_runbooks(
        self,
        *,
        target: str | None = None,
        alert_id: str | None = None,
        severity: str | None = None,
        alert_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        n = max(1, min(int(limit), 500))
        where: list[str] = []
        params: list[Any] = []
        if target:
            where.append("target = ?")
            params.append(str(target))
        if alert_id:
            where.append("alert_id = ?")
            params.append(str(alert_id))
        if severity:
            where.append("severity = ?")
            params.append(str(severity))
        if alert_type:
            where.append("alert_type = ?")
            params.append(str(alert_type))
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""

        conn = self._connect()
        rows = conn.execute(
            f"""
            SELECT runbook_id, alert_id, target, severity, alert_type,
                   title, summary,
                   root_cause_hypotheses_json, checklist_json,
                   recommended_actions_json, related_apis_json,
                     recovery_conditions_json,
                     observed_metrics_json, threshold_comparison_json,
                     created_at
            FROM scenario_router_runbooks
            {where_sql}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            [*params, n],
        ).fetchall()
        conn.close()

        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "runbook_id": str(r[0] or ""),
                    "alert_id": str(r[1] or ""),
                    "target": str(r[2] or ""),
                    "severity": str(r[3] or ""),
                    "alert_type": str(r[4] or ""),
                    "title": str(r[5] or ""),
                    "summary": str(r[6] or ""),
                    "root_cause_hypotheses": json.loads(r[7] or "[]"),
                    "checklist": json.loads(r[8] or "[]"),
                    "recommended_actions": json.loads(r[9] or "[]"),
                    "related_apis": json.loads(r[10] or "[]"),
                    "recovery_conditions": json.loads(r[11] or "[]"),
                    "observed_metrics": json.loads(r[12] or "{}"),
                    "threshold_comparison": json.loads(r[13] or "[]"),
                    "created_at": str(r[14] or ""),
                }
            )
        return out

    def has_executed_incident_action(self, *, alert_id: str, action_type: str) -> bool:
        aid = str(alert_id or "").strip()
        at = str(action_type or "").strip()
        if not aid or not at:
            return False
        conn = self._connect()
        row = conn.execute(
            """
            SELECT 1
            FROM scenario_router_incident_actions
            WHERE alert_id = ? AND action_type = ? AND status = 'EXECUTED'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (aid, at),
        ).fetchone()
        conn.close()
        return bool(row)

    def insert_incident_action(
        self,
        *,
        alert_id: str | None,
        runbook_id: str | None,
        target: str,
        action_type: str,
        status: str,
        dry_run: bool,
        requested_by: str,
        approved_by: str,
        result: dict[str, Any] | None,
        error_message: str | None,
        executed: bool,
    ) -> str:
        aid = f"sria_{uuid.uuid4().hex[:12]}"
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO scenario_router_incident_actions (
                action_id, alert_id, runbook_id,
                target, action_type, status,
                dry_run, requested_by, approved_by,
                result_json, error_message,
                created_at, executed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                aid,
                (str(alert_id) if alert_id else None),
                (str(runbook_id) if runbook_id else None),
                str(target or "win"),
                str(action_type),
                str(status),
                1 if bool(dry_run) else 0,
                str(requested_by or ""),
                str(approved_by or ""),
                json.dumps(result or {}, ensure_ascii=False, default=str),
                (str(error_message) if error_message else None),
                _now(),
                (_now() if bool(executed) else None),
            ),
        )
        conn.commit()
        conn.close()
        return aid

    def list_incident_actions(
        self,
        *,
        target: str | None = None,
        alert_id: str | None = None,
        runbook_id: str | None = None,
        action_type: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        n = max(1, min(int(limit), 500))
        where: list[str] = []
        params: list[Any] = []
        if target:
            where.append("target = ?")
            params.append(str(target))
        if alert_id:
            where.append("alert_id = ?")
            params.append(str(alert_id))
        if runbook_id:
            where.append("runbook_id = ?")
            params.append(str(runbook_id))
        if action_type:
            where.append("action_type = ?")
            params.append(str(action_type))
        if status:
            where.append("status = ?")
            params.append(str(status))
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""

        conn = self._connect()
        rows = conn.execute(
            f"""
            SELECT action_id, alert_id, runbook_id,
                   target, action_type, status,
                   dry_run, requested_by, approved_by,
                   result_json, error_message,
                   created_at, executed_at
            FROM scenario_router_incident_actions
            {where_sql}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            [*params, n],
        ).fetchall()
        conn.close()

        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "action_id": str(r[0] or ""),
                    "alert_id": str(r[1] or ""),
                    "runbook_id": str(r[2] or ""),
                    "target": str(r[3] or ""),
                    "action_type": str(r[4] or ""),
                    "status": str(r[5] or ""),
                    "dry_run": bool(int(r[6] or 0)),
                    "requested_by": str(r[7] or ""),
                    "approved_by": str(r[8] or ""),
                    "result": json.loads(r[9] or "{}"),
                    "error_message": str(r[10] or ""),
                    "created_at": str(r[11] or ""),
                    "executed_at": str(r[12] or ""),
                }
            )
        return out

    def get_incident_action_by_id(self, *, action_id: str) -> dict[str, Any] | None:
        aid = str(action_id or "").strip()
        if not aid:
            return None
        conn = self._connect()
        row = conn.execute(
            """
            SELECT action_id, alert_id, runbook_id,
                   target, action_type, status,
                   dry_run, requested_by, approved_by,
                   result_json, error_message,
                   created_at, executed_at
            FROM scenario_router_incident_actions
            WHERE action_id = ?
            LIMIT 1
            """,
            (aid,),
        ).fetchone()
        conn.close()
        if not row:
            return None
        return {
            "action_id": str(row[0] or ""),
            "alert_id": str(row[1] or ""),
            "runbook_id": str(row[2] or ""),
            "target": str(row[3] or ""),
            "action_type": str(row[4] or ""),
            "status": str(row[5] or ""),
            "dry_run": bool(int(row[6] or 0)),
            "requested_by": str(row[7] or ""),
            "approved_by": str(row[8] or ""),
            "result": json.loads(row[9] or "{}"),
            "error_message": str(row[10] or ""),
            "created_at": str(row[11] or ""),
            "executed_at": str(row[12] or ""),
        }

    def insert_incident_response(
        self,
        *,
        alert_id: str,
        runbook_id: str | None,
        target: str,
        severity: str,
        status: str,
        recommended_actions: list[dict[str, Any]] | None,
        notification_preview: dict[str, Any] | None,
        summary: dict[str, Any] | None,
    ) -> str:
        rid = f"srir_{uuid.uuid4().hex[:12]}"
        now = _now()
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO scenario_router_incident_responses (
                response_id, alert_id, runbook_id,
                target, severity, status,
                recommended_actions_json, notification_preview_json,
                summary_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rid,
                str(alert_id),
                (str(runbook_id) if runbook_id else None),
                str(target or "win"),
                str(severity or "WARNING"),
                str(status or "PREPARED"),
                json.dumps(recommended_actions or [], ensure_ascii=False, default=str),
                json.dumps(notification_preview or {}, ensure_ascii=False, default=str),
                json.dumps(summary or {}, ensure_ascii=False, default=str),
                now,
                now,
            ),
        )
        conn.commit()
        conn.close()
        return rid

    def get_incident_response_by_id(self, *, response_id: str) -> dict[str, Any] | None:
        rid = str(response_id or "").strip()
        if not rid:
            return None
        conn = self._connect()
        row = conn.execute(
            """
            SELECT response_id, alert_id, runbook_id,
                   target, severity, status,
                   recommended_actions_json, notification_preview_json,
                   summary_json, created_at, updated_at
            FROM scenario_router_incident_responses
            WHERE response_id = ?
            LIMIT 1
            """,
            (rid,),
        ).fetchone()
        conn.close()
        if not row:
            return None
        return {
            "response_id": str(row[0] or ""),
            "alert_id": str(row[1] or ""),
            "runbook_id": str(row[2] or ""),
            "target": str(row[3] or ""),
            "severity": str(row[4] or ""),
            "status": str(row[5] or ""),
            "recommended_actions": json.loads(row[6] or "[]"),
            "notification_preview": json.loads(row[7] or "{}"),
            "summary": json.loads(row[8] or "{}"),
            "created_at": str(row[9] or ""),
            "updated_at": str(row[10] or ""),
        }

    def list_incident_responses(
        self,
        *,
        target: str | None = None,
        alert_id: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        n = max(1, min(int(limit), 500))
        where: list[str] = []
        params: list[Any] = []
        if target:
            where.append("target = ?")
            params.append(str(target))
        if alert_id:
            where.append("alert_id = ?")
            params.append(str(alert_id))
        if status:
            where.append("status = ?")
            params.append(str(status))
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""

        conn = self._connect()
        rows = conn.execute(
            f"""
            SELECT response_id, alert_id, runbook_id,
                   target, severity, status,
                   recommended_actions_json, notification_preview_json,
                   summary_json, created_at, updated_at
            FROM scenario_router_incident_responses
            {where_sql}
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            [*params, n],
        ).fetchall()
        conn.close()

        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "response_id": str(r[0] or ""),
                    "alert_id": str(r[1] or ""),
                    "runbook_id": str(r[2] or ""),
                    "target": str(r[3] or ""),
                    "severity": str(r[4] or ""),
                    "status": str(r[5] or ""),
                    "recommended_actions": json.loads(r[6] or "[]"),
                    "notification_preview": json.loads(r[7] or "{}"),
                    "summary": json.loads(r[8] or "{}"),
                    "created_at": str(r[9] or ""),
                    "updated_at": str(r[10] or ""),
                }
            )
        return out

    def upsert_auto_recovery_policy(
        self,
        *,
        policy_id: str,
        alert_type: str,
        severity: str,
        action_type: str,
        auto_execute: bool,
        require_confirm: bool,
        enabled: bool,
    ) -> str:
        pid = str(policy_id or "").strip() or f"srarp_{uuid.uuid4().hex[:12]}"
        now = _now()
        conn = self._connect()
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
                pid,
                str(alert_type or "*"),
                str(severity or "*"),
                str(action_type or ""),
                1 if bool(auto_execute) else 0,
                1 if bool(require_confirm) else 0,
                1 if bool(enabled) else 0,
                now,
                now,
            ),
        )
        conn.commit()
        conn.close()
        return pid

    def list_auto_recovery_policies(
        self,
        *,
        alert_type: str | None = None,
        severity: str | None = None,
        action_type: str | None = None,
        enabled_only: bool = True,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        n = max(1, min(int(limit), 1000))
        where: list[str] = []
        params: list[Any] = []
        if alert_type:
            where.append("alert_type = ?")
            params.append(str(alert_type))
        if severity:
            where.append("severity = ?")
            params.append(str(severity))
        if action_type:
            where.append("action_type = ?")
            params.append(str(action_type))
        if bool(enabled_only):
            where.append("enabled = 1")
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""

        conn = self._connect()
        rows = conn.execute(
            f"""
            SELECT policy_id, alert_type, severity, action_type,
                   auto_execute, require_confirm, enabled,
                   created_at, updated_at
            FROM scenario_router_auto_recovery_policies
            {where_sql}
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            [*params, n],
        ).fetchall()
        conn.close()

        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "policy_id": str(r[0] or ""),
                    "alert_type": str(r[1] or ""),
                    "severity": str(r[2] or ""),
                    "action_type": str(r[3] or ""),
                    "auto_execute": bool(int(r[4] or 0)),
                    "require_confirm": bool(int(r[5] or 0)),
                    "enabled": bool(int(r[6] or 0)),
                    "created_at": str(r[7] or ""),
                    "updated_at": str(r[8] or ""),
                }
            )
        return out

    def insert_auto_recovery_execution(
        self,
        *,
        response_id: str | None,
        alert_id: str | None,
        action_type: str,
        status: str,
        auto_executed: bool,
        manual_required: bool,
        result: dict[str, Any] | None,
        error_message: str | None,
        executed: bool,
    ) -> str:
        eid = f"srare_{uuid.uuid4().hex[:12]}"
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO scenario_router_auto_recovery_executions (
                execution_id, response_id, alert_id,
                action_type, status,
                auto_executed, manual_required,
                result_json, error_message,
                created_at, executed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                eid,
                (str(response_id) if response_id else None),
                (str(alert_id) if alert_id else None),
                str(action_type or ""),
                str(status or ""),
                1 if bool(auto_executed) else 0,
                1 if bool(manual_required) else 0,
                json.dumps(result or {}, ensure_ascii=False, default=str),
                (str(error_message) if error_message else None),
                _now(),
                (_now() if bool(executed) else None),
            ),
        )
        conn.commit()
        conn.close()
        return eid

    def list_auto_recovery_executions(
        self,
        *,
        response_id: str | None = None,
        alert_id: str | None = None,
        action_type: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        n = max(1, min(int(limit), 500))
        where: list[str] = []
        params: list[Any] = []
        if response_id:
            where.append("response_id = ?")
            params.append(str(response_id))
        if alert_id:
            where.append("alert_id = ?")
            params.append(str(alert_id))
        if action_type:
            where.append("action_type = ?")
            params.append(str(action_type))
        if status:
            where.append("status = ?")
            params.append(str(status))
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""

        conn = self._connect()
        rows = conn.execute(
            f"""
            SELECT execution_id, response_id, alert_id,
                   action_type, status,
                   auto_executed, manual_required,
                   result_json, error_message,
                   created_at, executed_at
            FROM scenario_router_auto_recovery_executions
            {where_sql}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            [*params, n],
        ).fetchall()
        conn.close()

        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "execution_id": str(r[0] or ""),
                    "response_id": str(r[1] or ""),
                    "alert_id": str(r[2] or ""),
                    "action_type": str(r[3] or ""),
                    "status": str(r[4] or ""),
                    "auto_executed": bool(int(r[5] or 0)),
                    "manual_required": bool(int(r[6] or 0)),
                    "result": json.loads(r[7] or "{}"),
                    "error_message": str(r[8] or ""),
                    "created_at": str(r[9] or ""),
                    "executed_at": str(r[10] or ""),
                }
            )
        return out

    def get_auto_recovery_execution_by_id(self, *, execution_id: str) -> dict[str, Any] | None:
        eid = str(execution_id or "").strip()
        if not eid:
            return None
        conn = self._connect()
        row = conn.execute(
            """
            SELECT execution_id, response_id, alert_id,
                   action_type, status,
                   auto_executed, manual_required,
                   result_json, error_message,
                   created_at, executed_at
            FROM scenario_router_auto_recovery_executions
            WHERE execution_id = ?
            LIMIT 1
            """,
            (eid,),
        ).fetchone()
        conn.close()
        if not row:
            return None
        return {
            "execution_id": str(row[0] or ""),
            "response_id": str(row[1] or ""),
            "alert_id": str(row[2] or ""),
            "action_type": str(row[3] or ""),
            "status": str(row[4] or ""),
            "auto_executed": bool(int(row[5] or 0)),
            "manual_required": bool(int(row[6] or 0)),
            "result": json.loads(row[7] or "{}"),
            "error_message": str(row[8] or ""),
            "created_at": str(row[9] or ""),
            "executed_at": str(row[10] or ""),
        }
