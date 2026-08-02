from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from .contracts import (
    COMMIT_RE,
    ObservationContractError,
    canonical_sha256,
    feature_row_sha256,
    require_identifier,
    sha256_file,
    stable_uuid,
    timestamp,
    with_payload_sha256,
)


TRUE_VALUES = frozenset({"true", "1", "yes"})
FALSE_VALUES = frozenset({"", "false", "0", "no", "off"})
PROJECT_REF_RE = re.compile(r"^[a-z]{20}$")


@dataclass(frozen=True)
class ObservationConfig:
    enabled: bool
    app_env: str
    project_ref: str | None
    supabase_url: str | None
    candidate_commit_sha: str | None
    expanding_window_checks_passed: bool

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "ObservationConfig":
        values = os.environ if environ is None else environ
        raw_enabled = values.get("PHASE3N_OBSERVATION_ENABLED", "").strip().lower()
        if raw_enabled in TRUE_VALUES:
            enabled = True
        elif raw_enabled in FALSE_VALUES:
            enabled = False
        else:
            raise ObservationContractError("observation-enabled-invalid")
        app_env = values.get("APP_ENV", "").strip().lower()
        project_ref = values.get("PHASE3N_STAGING_PROJECT_REF", "").strip().lower() or None
        supabase_url = values.get("SUPABASE_URL", "").strip() or None
        commit = values.get("PHASE3N_CANDIDATE_COMMIT_SHA", "").strip().lower() or None
        raw_expanding = values.get(
            "PHASE3N_EXPANDING_WINDOW_CHECKS_PASSED", ""
        ).strip().lower()
        expanding_window_checks_passed = raw_expanding in TRUE_VALUES
        if raw_expanding not in TRUE_VALUES | FALSE_VALUES:
            raise ObservationContractError("expanding-window-check-invalid")
        config = cls(
            enabled,
            app_env,
            project_ref,
            supabase_url,
            commit,
            expanding_window_checks_passed,
        )
        if enabled:
            config.require_staging_boundary()
        return config

    def require_staging_boundary(self) -> None:
        if self.app_env != "staging":
            raise ObservationContractError("observation-staging-only")
        if self.project_ref is None or PROJECT_REF_RE.fullmatch(self.project_ref) is None:
            raise ObservationContractError("staging-project-ref-invalid")
        if self.supabase_url is None:
            raise ObservationContractError("staging-supabase-url-required")
        parsed = urlparse(self.supabase_url)
        if parsed.scheme != "https" or parsed.hostname != f"{self.project_ref}.supabase.co":
            raise ObservationContractError("staging-supabase-project-mismatch")
        if self.candidate_commit_sha is None or COMMIT_RE.fullmatch(self.candidate_commit_sha) is None:
            raise ObservationContractError("candidate-commit-invalid")
        if self.expanding_window_checks_passed is not True:
            raise ObservationContractError("expanding-window-check-required")


def _one(response: Any, *, code: str) -> dict[str, Any]:
    data = getattr(response, "data", None)
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise ObservationContractError(code)
    return dict(data[0])


class ObservationGateway:
    def __init__(self, client: Any) -> None:
        if client is None or not callable(getattr(client, "rpc", None)):
            raise ObservationContractError("observation-client-unavailable")
        self._client = client

    def _rpc(self, name: str, payload: dict[str, Any], *, code: str) -> dict[str, Any]:
        try:
            return _one(self._client.rpc(name, payload).execute(), code=code)
        except ObservationContractError:
            raise
        except Exception as exc:
            raise ObservationContractError(code) from exc

    def register_manifest(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = self._rpc(
            "register_phase3n_model_manifest",
            {"p_manifest": payload},
            code="model-manifest-write-failed",
        )
        if row.get("mutation_code") not in {"inserted", "duplicate"}:
            raise ObservationContractError("model-manifest-conflict")
        return row

    def record_prediction(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = self._rpc(
            "record_phase3n_prediction_observation",
            {"p_observation": payload},
            code="prediction-observation-write-failed",
        )
        if row.get("mutation_code") not in {"inserted", "duplicate"}:
            raise ObservationContractError("prediction-observation-rejected")
        return row

    def record_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = self._rpc(
            "record_phase3n_result_observation",
            {"p_result": payload},
            code="result-observation-write-failed",
        )
        if row.get("mutation_code") not in {"inserted", "duplicate"}:
            raise ObservationContractError("result-observation-rejected")
        return row

    def select(self, table: str, columns: str = "*", *, max_rows: int = 100_000) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page_size = 1_000
        try:
            while len(rows) < max_rows:
                response = (
                    self._client.table(table)
                    .select(columns)
                    .range(len(rows), min(len(rows) + page_size - 1, max_rows - 1))
                    .execute()
                )
                data = getattr(response, "data", None)
                if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
                    raise ObservationContractError("observation-read-invalid")
                rows.extend(dict(row) for row in data)
                if len(data) < page_size:
                    break
        except ObservationContractError:
            raise
        except Exception as exc:
            raise ObservationContractError("observation-read-failed") from exc
        if len(rows) >= max_rows:
            raise ObservationContractError("observation-read-limit-reached")
        return rows

    def select_in(
        self,
        table: str,
        columns: str,
        key: str,
        values: Iterable[str],
    ) -> list[dict[str, Any]]:
        items = list(dict.fromkeys(str(value) for value in values))
        rows: list[dict[str, Any]] = []
        try:
            for offset in range(0, len(items), 100):
                response = (
                    self._client.table(table)
                    .select(columns)
                    .in_(key, items[offset : offset + 100])
                    .execute()
                )
                data = getattr(response, "data", None)
                if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
                    raise ObservationContractError("observation-read-invalid")
                rows.extend(dict(row) for row in data)
        except ObservationContractError:
            raise
        except Exception as exc:
            raise ObservationContractError("observation-read-failed") from exc
        return rows


def build_model_manifest_payload(
    *,
    model_id: str,
    model_version: str,
    model_path: Path,
    candidate_commit_sha: str,
    feature_columns: Iterable[str],
    training_data_ended_at: datetime,
    environment: str = "staging",
    expanding_window_checks_passed: bool,
) -> dict[str, Any]:
    columns = [str(column) for column in feature_columns]
    if not columns or len(columns) != len(set(columns)):
        raise ObservationContractError("model-feature-columns-invalid")
    require_identifier(model_id, code="model-id-invalid")
    require_identifier(model_version, code="model-version-invalid")
    if COMMIT_RE.fullmatch(candidate_commit_sha) is None:
        raise ObservationContractError("candidate-commit-invalid")
    if environment != "staging":
        raise ObservationContractError("observation-staging-only")
    if expanding_window_checks_passed is not True:
        raise ObservationContractError("expanding-window-check-required")
    artifact_sha = sha256_file(model_path)
    feature_sha = canonical_sha256(columns)
    base = {
        "idempotency_key": f"model:{artifact_sha}:{feature_sha}:{candidate_commit_sha}",
        "model_id": model_id,
        "model_version": model_version,
        "model_artifact_sha256": artifact_sha,
        "candidate_commit_sha": candidate_commit_sha,
        "feature_manifest_sha256": feature_sha,
        "model_feature_columns": columns,
        "training_data_ended_at": timestamp(training_data_ended_at),
        "expanding_window_checks_passed": True,
        "source_environment": environment,
    }
    return with_payload_sha256(base)


def build_prediction_payload(
    *,
    manifest_id: str,
    race_id: str,
    horse_id: str,
    horse_number: int,
    race_date: str,
    data_observed_at: datetime,
    data_cutoff_at: datetime,
    feature_columns: Iterable[str],
    feature_values: Iterable[Any],
    predicted_value: float,
    predicted_probability: float,
    predicted_rank: int,
    odds_at_prediction: float | None,
    recommendation: str = "unavailable",
    qualifying_bet: bool = False,
    wager_amount: float = 0.0,
    baseline_wager_amount: float = 0.0,
    latency_ms: float = 0.0,
) -> dict[str, Any]:
    if data_observed_at.tzinfo is None or data_cutoff_at.tzinfo is None:
        raise ObservationContractError("prediction-source-timezone-required")
    if data_observed_at > data_cutoff_at:
        raise ObservationContractError("prediction-source-temporal-order-invalid")
    feature_sha = feature_row_sha256(feature_columns, feature_values)
    base = {
        "manifest_id": manifest_id,
        "race_id": str(race_id),
        "horse_id": str(horse_id),
        "horse_number": int(horse_number),
        "race_date": race_date,
        "data_observed_at": timestamp(data_observed_at),
        "data_cutoff_at": timestamp(data_cutoff_at),
        "feature_values_sha256": feature_sha,
        "predicted_value": float(predicted_value),
        "predicted_probability": float(predicted_probability),
        "predicted_rank": int(predicted_rank),
        "odds_at_prediction": None if odds_at_prediction is None else float(odds_at_prediction),
        "recommendation": recommendation,
        "qualifying_bet": bool(qualifying_bet),
        "wager_amount": float(wager_amount),
        "baseline_wager_amount": float(baseline_wager_amount),
        "latency_ms": float(latency_ms),
        "source_environment": "staging",
        "leakage_violation_count": 0,
    }
    digest = canonical_sha256(base)
    base["observation_id"] = stable_uuid("prediction", digest)
    base["idempotency_key"] = f"prediction:{digest}"
    return with_payload_sha256(base)


def build_result_payload(
    *,
    observation_id: str,
    settled_at: datetime,
    y_true: int,
    finish_order: int | None,
    bet_outcome: str,
    return_amount: float,
    baseline_return_amount: float,
) -> dict[str, Any]:
    base = {
        "observation_id": observation_id,
        "settled_at": timestamp(settled_at),
        "y_true": int(y_true),
        "finish_order": finish_order,
        "bet_outcome": bet_outcome,
        "return_amount": float(return_amount),
        "baseline_return_amount": float(baseline_return_amount),
    }
    digest = canonical_sha256(base)
    base["idempotency_key"] = f"result:{observation_id}:{digest}"
    return with_payload_sha256(base)


def join_progress_rows(gateway: ObservationGateway) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    manifests = {
        row["manifest_id"]: row
        for row in gateway.select(
            "phase3n_model_manifests",
            "manifest_id,model_version,candidate_commit_sha,model_id,model_artifact_sha256,feature_manifest_sha256,model_feature_columns,training_data_ended_at",
        )
    }
    predictions = gateway.select("phase3n_prediction_observations")
    for row in predictions:
        manifest = manifests.get(row.get("manifest_id"), {})
        row.update(
            model_version=manifest.get("model_version"),
            candidate_commit_sha=manifest.get("candidate_commit_sha"),
            model_id=manifest.get("model_id"),
            model_artifact_sha256=manifest.get("model_artifact_sha256"),
            feature_manifest_sha256=manifest.get("feature_manifest_sha256"),
            model_feature_columns=manifest.get("model_feature_columns"),
            training_data_ended_at=manifest.get("training_data_ended_at"),
        )
    results = gateway.select("phase3n_result_observation_events")
    attempts = gateway.select("phase3n_observation_ingest_attempts")
    return predictions, results, attempts
