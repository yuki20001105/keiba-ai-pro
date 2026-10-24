"""Opt-in bounded local candidate serving proof, NOT ROI or release completion.

Default execution performs no credential, network or source-database reads.
--execute authorizes one ordinary identity-provider login, two fixed local GETs
and ONE fixed prediction POST. Prediction may append the authenticated owner's
performance snapshot and prediction_log; no actual/paper ticket, collection,
training, activation, settlement, collector or restart endpoint is called.
Secrets, response bodies, identities and raw exception messages are never saved.
The app origin/model/race cannot be changed through arguments. File settings
are checked by the existing auth helper, but are not proof of the running
server's upstream environment. Only a new scoped evidence JSON is written here.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import sqlite3
import time
from urllib.error import HTTPError
from urllib.request import Request


ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "candidate_smoke_readonly_auth", ROOT / "scripts/smoke_local_authenticated_readonly.py")
assert _SPEC and _SPEC.loader
_AUTH = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_AUTH)
configuration = _AUTH.configuration
secure_opener = _AUTH.secure_opener
authenticate = _AUTH.authenticate
SafeSmokeError = _AUTH.SafeSmokeError

ORIGIN = "http://127.0.0.1:3000"
RACE_ID = "202605040301"
MODEL_ID = "local_safe_m02_2025"
RUNNERS = 16
FEATURES = 47
MAX_BODY_BYTES = 2 * 1024 * 1024
TOTAL_NETWORK_SECONDS = 90
GET_TIMEOUT_SECONDS = 8
GET_ENDPOINTS = ("/api/models", "/api/races/by-date?date=20261010")
PREDICT_ENDPOINT = "/api/analyze-race"
OUTPUT_RELATIVE = Path("reports/generated/local-release-smoke")
BET_TYPES = {"単勝", "ワイド", "馬連", "馬単", "三連複", "三連単"}
BET_CARDINALITIES = {"単勝": 1, "ワイド": 2, "馬連": 2, "馬単": 2, "三連複": 3, "三連単": 3}


def _sha(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _file_sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def protected_state(root: Path) -> dict:
    """A coherent RO hash of original target rows, excluding prediction logs."""
    db = root / "keiba/data/keiba_ultimate.db"
    pointer = root / "python-api/models/.active_model.json"
    if db.is_symlink() or pointer.is_symlink():
        raise SafeSmokeError("PROTECTED_STATE_REJECTED")
    database = sqlite3.connect(db.resolve(strict=True).as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        database.execute("PRAGMA query_only=ON")
        database.execute("BEGIN")
        rows = {}
        for table in ("races_ultimate", "race_results_ultimate", "return_tables_ultimate"):
            # Table identifiers are an immutable local allowlist, not inputs.
            order = "race_id" if table == "races_ultimate" else "id"
            cursor = database.execute(f"SELECT * FROM {table} WHERE race_id=? ORDER BY {order}", (RACE_ID,))
            rows[table] = {"columns": [column[0] for column in cursor.description],
                           "rows": [list(row) for row in cursor]}
    finally:
        database.close()
    if len(rows["races_ultimate"]["rows"]) != 1 or len(rows["race_results_ultimate"]["rows"]) != RUNNERS:
        raise SafeSmokeError("SOURCE_COHORT_REJECTED")
    content = pointer.read_bytes()
    model_id = json.loads(content).get("model_id")
    if not isinstance(model_id, str) or not re.fullmatch(r"model_[A-Za-z0-9_-]{1,180}", model_id):
        raise SafeSmokeError("PROTECTED_STATE_REJECTED")
    artifact = pointer.parent / (model_id + ".joblib")
    if artifact.is_symlink() or not artifact.is_file():
        raise SafeSmokeError("PROTECTED_STATE_REJECTED")
    return {"target_source_rows_sha256": _sha(rows),
            "source_race_rows": 1, "source_runner_rows": RUNNERS,
            "source_dividend_rows": len(rows["return_tables_ultimate"]["rows"]),
            "active_pointer_sha256": hashlib.sha256(content).hexdigest(),
            "active_artifact_sha256": _file_sha(artifact)}


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SafeSmokeError("DEADLINE_EXCEEDED")
    return remaining


def bounded_json(response, deadline: float):
    read = getattr(response, "read1", None) or response.read
    pieces, length = [], 0
    while True:
        remaining = _remaining(deadline)
        # urllib's HTTPResponse uses a SocketIO below its buffered file. Reset
        # timeout before every read so a dribbling body cannot reset 90 seconds.
        sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
        if sock is not None:
            sock.settimeout(remaining)
        chunk = read(min(16384, MAX_BODY_BYTES + 1 - length))
        _remaining(deadline)
        if not chunk:
            break
        length += len(chunk)
        if length > MAX_BODY_BYTES:
            raise SafeSmokeError("RESPONSE_REJECTED")
        pieces.append(chunk)
    try:
        def reject_constant(_value):
            raise ValueError("JSON nonfinite number rejected")
        value = json.loads(b"".join(pieces), parse_constant=reject_constant)
        if not isinstance(value, dict):
            raise ValueError("JSON object required")
        return value
    except (ValueError, UnicodeError):
        raise SafeSmokeError("RESPONSE_REJECTED") from None


def fixed_request(endpoint: str, token: str) -> Request:
    if endpoint not in (*GET_ENDPOINTS, PREDICT_ENDPOINT):
        raise SafeSmokeError("ENDPOINT_REJECTED")
    if not isinstance(token, str) or len(token) > _AUTH.MAX_HEADER_SECRET_LENGTH or not _AUTH._JWT.fullmatch(token):
        raise SafeSmokeError("TOKEN_REJECTED")
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    if endpoint == PREDICT_ENDPOINT:
        body = json.dumps({"race_id": RACE_ID, "model_id": MODEL_ID,
                           "include_explanation": True}, separators=(",", ":")).encode()
        headers["Content-Type"] = "application/json"
        return Request(ORIGIN + endpoint, data=body, method="POST", headers=headers)
    return Request(ORIGIN + endpoint, method="GET", headers=headers)


def request_json(opener, endpoint: str, token: str, deadline: float):
    request = fixed_request(endpoint, token)
    try:
        timeout = min(_remaining(deadline), TOTAL_NETWORK_SECONDS if endpoint == PREDICT_ENDPOINT else GET_TIMEOUT_SECONDS)
        with opener.open(request, timeout=timeout) as response:
            status = response.status
            if type(status) is not int or not 100 <= status <= 599:
                return "RESPONSE_REJECTED", None
            if status != 200:
                return status, None
            return status, bounded_json(response, deadline)
    except HTTPError as exc:
        status = exc.code if type(exc.code) is int and 100 <= exc.code <= 599 else "RESPONSE_REJECTED"
        exc.close()  # Never read private error/redirect response bodies.
        return status, None
    except SafeSmokeError:
        return "RESPONSE_REJECTED", None
    except Exception:
        return "TRANSPORT_FAILED", None


def _number(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def validate_prediction(body: dict) -> dict:
    """Persist only fixed checks/counts, never arbitrary response values/text."""
    diagnostic = body.get("input_diagnostics") or {}
    reference = body.get("reference_bets") or {}
    recommendation = body.get("recommendation") or {}
    predictions = body.get("predictions") or []
    if not isinstance(diagnostic, dict):
        diagnostic = {}
    if not isinstance(reference, dict):
        reference = {}
    if not isinstance(recommendation, dict):
        recommendation = {}
    if not isinstance(predictions, list):
        predictions = []
    valid_predictions = len(predictions) == RUNNERS and all(isinstance(row, dict) for row in predictions)
    if valid_predictions:
        numbers = [row.get("horse_number") for row in predictions]
        ranks = [row.get("predicted_rank") for row in predictions]
        valid_predictions = (all(type(value) is int and 1 <= value <= 18 for value in numbers)
            and len(set(numbers)) == RUNNERS and all(type(value) is int for value in ranks)
            and ranks == list(range(1, RUNNERS + 1))
            and all(_number(row.get("p_raw")) for row in predictions)
            and all(a["p_raw"] >= b["p_raw"] for a, b in zip(predictions, predictions[1:])))
    shap_count = 0
    for row in predictions:
        if not isinstance(row, dict):
            continue
        explanation = row.get("explanation") or {}
        if not isinstance(explanation, dict):
            continue
        features = explanation.get("features")
        if (explanation.get("method") == "tree_shap" and _number(explanation.get("base_value"))
                and isinstance(features, list) and features
                and all(isinstance(item, dict) and _number(item.get("contribution")) for item in features)):
            shap_count += 1
    tickets = reference.get("tickets")
    reference_valid = (isinstance(tickets, list) and len(tickets) == len(BET_TYPES)
        and all(isinstance(ticket, dict) and set(ticket) == {"bet_type", "combination", "horse_numbers"}
                and isinstance(ticket.get("bet_type"), str) and ticket["bet_type"] in BET_TYPES
                and isinstance(ticket.get("combination"), str)
                and isinstance(ticket.get("horse_numbers"), list)
                and len(ticket["horse_numbers"]) == BET_CARDINALITIES[ticket["bet_type"]]
                and len(set(ticket["horse_numbers"])) == len(ticket["horse_numbers"])
                and all(type(number) is int and 1 <= number <= 18 for number in ticket["horse_numbers"])
                and ticket["combination"] == "-".join(map(str, ticket["horse_numbers"]))
                for ticket in tickets)
        and {ticket["bet_type"] for ticket in tickets} == BET_TYPES)
    checks = {
        "success": body.get("success") is True,
        "explicit_candidate_model": body.get("model_id") == MODEL_ID,
        "target_race": isinstance(body.get("race_info"), dict) and body["race_info"].get("race_id") == RACE_ID,
        "runner_identity_count": valid_predictions,
        "input_contract_schema": diagnostic.get("schema") == "prediction-input-contract-v1",
        "input_columns_exact_47": type(diagnostic.get("expected_count")) is int
            and type(diagnostic.get("actual_count")) is int
            and diagnostic["expected_count"] == diagnostic["actual_count"] == FEATURES,
        "no_missing_columns": diagnostic.get("missing_columns") == [],
        "semantic_parity_verified": diagnostic.get("semantic_parity_verified") is True,
        "candidate_source_contract": diagnostic.get("scope") == "local-safe-candidate-input-v1"
            and diagnostic.get("source_hash_matches") is True and diagnostic.get("history_available") is True
            and diagnostic.get("blocked_columns") == []
            and "card_capture_provenance_unverified" in diagnostic.get("issue_codes", []),
        "live_capture_remains_unverified": diagnostic.get("status") == "unverified"
            and diagnostic.get("live_capture_hash_bound") is False,
        "no_current_outcomes_or_future_history": diagnostic.get("current_outcomes_consumed") is False
            and diagnostic.get("same_day_or_future_history_used") is False,
        "not_calibrated_or_profit_proven": diagnostic.get("score_is_probability") is False
            and diagnostic.get("calibration_verified") is False and diagnostic.get("profitability_verified") is False,
        "reference_schema": reference.get("schema") == "rank-reference-bets-v1" and reference.get("basis") == "ranking_only",
        "reference_is_nonmonetary": reference.get("purchase_eligible") is False
            and reference.get("monetary_amounts_assigned") is False,
        "all_six_reference_types_only": bool(reference_valid),
        "monetary_recommendation_stays_skip": recommendation.get("decision") == "skip"
            and recommendation.get("tickets") == [] and recommendation.get("purchase_count") == 0
            and recommendation.get("unit_price") == 0 and recommendation.get("total_cost") == 0
            and recommendation.get("expected_return") == 0,
        "same_16_runner_tree_shap": body.get("explanation_method") == "tree_shap" and shap_count == RUNNERS,
    }
    return {"checks": checks, "passed": all(checks.values()),
            "prediction_count": len(predictions), "tree_shap_count": shap_count,
            "reference_type_count": len(tickets) if isinstance(tickets, list) else None,
            "semantic_feature_parity_is_not_live_cutoff_or_profitability_proof": True}


def run_smoke(*, execute: bool, root: Path = ROOT, opener=None) -> dict:
    report = {
        "schema": "local-safe-candidate-serving-smoke-v1",
        "scope": "one authenticated local candidate prediction; not acquisition/training/settlement E2E or ROI evaluation",
        "target_race_id": RACE_ID, "explicit_model_id": MODEL_ID,
        "authentication_status": "NOT_RUN", "prediction_status": "NOT_RUN",
        "probes": [{"endpoint": endpoint, "status": "NOT_RUN", "count": None} for endpoint in GET_ENDPOINTS],
        "network_deadline_seconds": TOTAL_NETWORK_SECONDS, "response_max_bytes": MAX_BODY_BYTES,
        "count_scope": "response_list_length_only_not_total_inventory",
        "allowed_prediction_side_effects": ["authenticated_owner_performance_snapshot", "prediction_log"],
        "prediction_post_attempts": 0, "actual_ticket_posts": 0, "paper_ticket_posts": 0,
        "collection_calls": 0, "training_calls": 0, "activation_calls": 0, "restart_calls": 0,
        "collector_not_accessed": True, "running_upstream_environment_binding_verified": False,
        "protected_state_before": None, "protected_state_after": None, "protected_state_unchanged": False,
        "serving_gate_passed": False, "release_complete": False,
        "all_five_features_completed": False, "profitability_validated": False,
    }
    if not execute:
        return report
    token = None
    try:
        before = protected_state(root)
        report["protected_state_before"] = before
        config = configuration(root)
        opener = opener or secure_opener()
        deadline = time.monotonic() + TOTAL_NETWORK_SECONDS
        status, token = authenticate(opener, config)
        config = None
        report["authentication_status"] = status
        if status != 200 or token is None:
            return report
        for endpoint, key in zip(GET_ENDPOINTS, ("models", "races")):
            status, body = request_json(opener, endpoint, token, deadline)
            collection = body.get(key) if isinstance(body, dict) else None
            count = len(collection) if isinstance(collection, list) else None
            probe = {"endpoint": endpoint, "status": status, "count": count}
            if status == 200 and count is None:
                probe["status"] = "RESPONSE_REJECTED"
            if key == "models":
                probe["explicit_candidate_listed"] = isinstance(collection, list) and any(
                    isinstance(row, dict) and row.get("model_id") == MODEL_ID for row in collection)
            report["probes"][0 if key == "models" else 1] = probe
        probes = report["probes"]
        if (any(probe["status"] != 200 for probe in probes)
                or not probes[0].get("explicit_candidate_listed")
                or [probe["count"] for probe in probes] != [8, 24]):
            report["failure_code"] = "EXPECTED_LOCAL_LISTS_NOT_CONFIRMED"
            return report
        _remaining(deadline)
        report["prediction_post_attempts"] = 1
        started = time.monotonic()
        status, body = request_json(opener, PREDICT_ENDPOINT, token, deadline)
        report["prediction_elapsed_seconds"] = round(time.monotonic() - started, 3)
        report["prediction_status"] = status
        if status == 200 and isinstance(body, dict):
            report["prediction_validation"] = validate_prediction(body)
            timing = body.get("processing_timings") or {}
            report["reported_compute_timings"] = {
                key: timing[key] for key in (
                    "model_load_seconds", "history_seconds", "features_seconds", "inference_seconds",
                    "explanation_seconds", "worker_seconds", "queue_seconds", "elapsed_seconds", "workers",
                ) if isinstance(timing, dict) and key in timing and _number(timing[key]) and timing[key] >= 0
            }
            report["timing_scope"] = "response timings may be reused from the response cache; transport duration is separate"
            # Only presence, not the owner's private snapshot ID, is retained.
            report["personal_snapshot_id_present"] = bool(body.get("performance_snapshot_id"))
        body = None
    except SafeSmokeError:
        report["failure_code"] = "SAFE_PREFLIGHT_OR_DEADLINE_REJECTED"
    except Exception:
        report["failure_code"] = "DIAGNOSTIC_FAILED"
    finally:
        token = None
        if report["protected_state_before"] is not None:
            try:
                after = protected_state(root)
                report["protected_state_after"] = after
                report["protected_state_unchanged"] = after == report["protected_state_before"]
            except Exception:
                report["failure_code"] = "PROTECTED_STATE_RECHECK_FAILED"
        report["serving_gate_passed"] = (report["prediction_status"] == 200
            and report.get("prediction_validation", {}).get("passed") is True
            and report["protected_state_unchanged"] and "failure_code" not in report)
    return report


def report_path(root: Path, supplied: Path | None = None) -> Path:
    allowed = (root / OUTPUT_RELATIVE).resolve()
    if not allowed.is_relative_to(root.resolve()):
        raise SafeSmokeError("OUTPUT_REJECTED")
    target = supplied or allowed / ("safe-serving-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    target = target.resolve()
    if (not target.is_relative_to(allowed) or target.suffix != ".json"
            or not target.name.startswith("safe-serving-") or target.exists()):
        raise SafeSmokeError("OUTPUT_REJECTED")
    return target


def write_report(path: Path, report: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Authorize ONE fixed local prediction; snapshot/log writes may occur.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        output = report_path(ROOT, args.output)  # Validate before auth/settings.
        report = run_smoke(execute=args.execute)
        write_report(output, report)
    except Exception:
        print("Local candidate serving proof: OUTPUT_REJECTED")
        return 1
    print("Evidence: " + str(output.relative_to(ROOT)))
    print("Serving gate only; live cutoff, ROI and five-feature completion NOT established.")
    return 0 if report["serving_gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
