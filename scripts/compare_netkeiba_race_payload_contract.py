from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT_DIR / "reports"
DRY_RUN_REPORT_PATH = REPORTS_DIR / "netkeiba_race_dry_run_smoke_result.json"
OUTPUT_PATH = REPORTS_DIR / "netkeiba_race_payload_contract_diff.json"

EXPECTED_CONTRACT: dict[str, dict[str, str]] = {
    "races": {
        "race_id": "str",
        "race_name": "str",
        "venue": "str",
        "distance": "int",
        "track_type": "str",
        "weather": "str",
        "field_condition": "str",
        "user_id": "str",
    },
    "race_results": {
        "race_id": "str",
        "finish_position": "int",
        "bracket_number": "int",
        "horse_number": "int",
        "horse_name": "str",
        "sex": "str",
        "age": "int",
        "jockey_weight": "float",
        "jockey_name": "str",
        "finish_time": "str",
        "odds": "float",
        "popularity": "int",
        "user_id": "str",
    },
    "race_payouts": {
        "race_id": "str",
        "bet_type": "str",
        "combination": "str",
        "payout": "int",
        "user_id": "str",
    },
}


def _normalize(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _extract_payload(report: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(report, dict):
        return None
    check = report.get("check")
    if not isinstance(check, dict):
        return None
    body = check.get("body")
    if isinstance(body, dict) and isinstance(body.get("detail"), dict):
        return body["detail"]
    if isinstance(body, dict):
        return body
    return None


def _actual_type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if value is None:
        return "none"
    return type(value).__name__


def _type_compatible(expected: str, actual: str) -> bool:
    if expected == actual:
        return True
    if expected == "float" and actual == "int":
        return True
    return False


def _classify_table(table: str, expected: dict[str, str], actual_record: dict[str, Any] | None) -> dict[str, Any]:
    actual_record = actual_record or {}
    expected_fields = list(expected.keys())
    actual_fields = list(actual_record.keys())

    missing = [k for k in expected_fields if k not in actual_record]
    extra = [k for k in actual_fields if k not in expected]

    naming_mismatch: list[dict[str, str]] = []
    missing_after_name = []
    extra_after_name = set(extra)
    actual_norm_map = {_normalize(k): k for k in actual_fields}

    for m in missing:
        hit = actual_norm_map.get(_normalize(m))
        if hit and hit in extra_after_name:
            naming_mismatch.append({"expected": m, "actual": hit})
            extra_after_name.remove(hit)
        else:
            missing_after_name.append(m)

    type_mismatch: list[dict[str, str]] = []
    compatible: list[str] = []
    unknown: list[str] = []

    for key, exp_t in expected.items():
        if key not in actual_record:
            continue
        value = actual_record[key]
        act_t = _actual_type_name(value)
        if act_t == "none":
            unknown.append(key)
            continue
        if _type_compatible(exp_t, act_t):
            compatible.append(key)
        else:
            type_mismatch.append({"field": key, "expected": exp_t, "actual": act_t})

    return {
        "table": table,
        "compatible": compatible,
        "missing_in_dry_run": missing_after_name,
        "extra_in_dry_run": sorted(extra_after_name),
        "naming_mismatch": naming_mismatch,
        "type_mismatch": type_mismatch,
        "unknown": unknown,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare Next write payload contract with FastAPI dry-run preview")
    parser.add_argument("--dry-run-report", default=str(DRY_RUN_REPORT_PATH), help="Path to dry-run smoke report JSON")
    args = parser.parse_args()

    report_path = Path(args.dry_run_report)
    smoke_report = _read_json(report_path)

    result: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "success": False,
        "verdict": "fail",
        "verdict_reason": "contract-error",
        "input": {
            "dry_run_report": str(report_path),
        },
        "next_contract": EXPECTED_CONTRACT,
        "dry_run_status": None,
        "comparisons": [],
        "summary": {
            "compatible": 0,
            "missing_in_dry_run": 0,
            "extra_in_dry_run": 0,
            "naming_mismatch": 0,
            "type_mismatch": 0,
            "unknown": 0,
        },
    }

    if not isinstance(smoke_report, dict):
        result["verdict_reason"] = "dry-run smoke report missing or invalid JSON"
    else:
        contract_ok = bool(smoke_report.get("contract_ok", False))
        payload = _extract_payload(smoke_report)

        if not contract_ok or not isinstance(payload, dict):
            result["verdict_reason"] = "dry-run contract broken or body missing"
        else:
            dry_run_status = str(payload.get("status") or "")
            result["dry_run_status"] = dry_run_status

            if payload.get("can_write") is not False or payload.get("write_performed") is not False or payload.get("dry_run") is not True:
                result["verdict_reason"] = "dry-run safety flags invalid"
            else:
                preview = payload.get("preview")
                tables = preview.get("tables") if isinstance(preview, dict) else None
                table_map: dict[str, dict[str, Any]] = {}
                if isinstance(tables, list):
                    for t in tables:
                        if not isinstance(t, dict):
                            continue
                        name = str(t.get("target_table") or "")
                        if name:
                            table_map[name] = t

                if dry_run_status != "ready":
                    if dry_run_status in {"degraded", "unavailable", "invalid"}:
                        result["verdict"] = "warn"
                        result["verdict_reason"] = f"dry-run-status-{dry_run_status}"
                        result["success"] = True
                    else:
                        result["verdict"] = "fail"
                        result["verdict_reason"] = "invalid-dry-run-status"
                else:
                    for table, expected in EXPECTED_CONTRACT.items():
                        sample = None
                        info = table_map.get(table)
                        if isinstance(info, dict):
                            samples = info.get("sample_records")
                            if isinstance(samples, list) and samples and isinstance(samples[0], dict):
                                sample = samples[0]
                        cmp = _classify_table(table, expected, sample)
                        result["comparisons"].append(cmp)

                        result["summary"]["compatible"] += len(cmp["compatible"])
                        result["summary"]["missing_in_dry_run"] += len(cmp["missing_in_dry_run"])
                        result["summary"]["extra_in_dry_run"] += len(cmp["extra_in_dry_run"])
                        result["summary"]["naming_mismatch"] += len(cmp["naming_mismatch"])
                        result["summary"]["type_mismatch"] += len(cmp["type_mismatch"])
                        result["summary"]["unknown"] += len(cmp["unknown"])

                    has_structural_diff = any(
                        result["summary"][k] > 0
                        for k in ["missing_in_dry_run", "extra_in_dry_run", "naming_mismatch", "type_mismatch"]
                    )

                    if not has_structural_diff:
                        result["verdict"] = "pass"
                        result["verdict_reason"] = "contracts-compatible"
                        result["success"] = True
                    else:
                        result["verdict"] = "warn"
                        result["verdict_reason"] = "contract-diff-detected"
                        result["success"] = True

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "result_file": str(OUTPUT_PATH),
        "success": result["success"],
        "verdict": result["verdict"],
        "verdict_reason": result["verdict_reason"],
        "dry_run_status": result["dry_run_status"],
        "summary": result["summary"],
    }, ensure_ascii=False))

    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
