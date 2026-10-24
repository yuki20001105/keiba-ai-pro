"""Seal the isolated read-only quality audit, without repairing or training."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from evaluate_db_shared_venues import preservation_receipt


def sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-dir", required=True, type=Path)
    args = parser.parse_args()
    directory = args.audit_dir.resolve(strict=True)
    receipt = read(directory / "snapshot_receipt.json")
    coverage = read(directory / "acquisition_coverage.json")
    runners = read(directory / "runner_quality.json")
    model = read(directory / "model_quality.json")
    premium = read(directory / "premium_inventory.json")
    result_clarification = read(directory / "acquisition_result_status_clarification.json")
    decomposition = read(directory / "learning_quality_decomposition.json")
    snapshot = Path(receipt["snapshot_path"])
    if snapshot.parent != directory or sha(snapshot) != receipt["snapshot_sha256"]:
        raise ValueError("snapshot-body-changed")
    protected_after = preservation_receipt()
    protected_before = receipt["protected_before"]
    changed = {key: {"before": protected_before.get(key), "after": protected_after.get(key)}
               for key in set(protected_before) | set(protected_after)
               if protected_before.get(key) != protected_after.get(key)}
    if any(key != "forward_processes" for key in changed):
        raise ValueError("protected-file-or-model-changed; inspect before claiming preservation")
    scope = runners["totals"]["jra_flat_201601_202609"]
    assert coverage["summary"]["saved_races"] == runners["counts"]["races_ultimate"]
    assert scope["source_rows"] == 501420 and scope["races"] == 35811
    summary = {
        "schema": "current-data-quality-conclusion-v1",
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "conclusion_ja": "全件取得・全項目充足とは言えない。基礎データの識別は良好だが、網羅性、結果の更新、正規化、補助項目、発走前の時点証明に課題がある。",
        "snapshot_sha256": receipt["snapshot_sha256"],
        "all_saved": {"races": 79664, "runner_rows": 958607},
        "acquisition_summary_original_parser": coverage["summary"],
        "acquisition_status_clarification": result_clarification,
        "clarification_precedence": "For raw result status use the separate clarification, not raw-unsettled counts which include adjudicated finish notation.",
        "jra_flat_20160101_to_20260930": {
            "races": scope["races"], "runner_rows": scope["source_rows"],
            "fields": scope["fields"], "semantic_context": scope.get("semantic_context"),
            "not_same_denominator_as_jra_flat_plus_jump_official_race_count": True,
        },
        "learning": {
            "complete_eligible_2016_2026": model["current_interpretation"],
            "complete_history_latest": model["eligible_complete_history_date_range"]["to"],
            "eligibility_decomposition": decomposition,
            "nonfinish_or_scope_exclusion_is_not_automatically_scrape_failure": True,
            "46_80_input_scope": premium["model_input_scope"],
        },
        "premium_cautions": {
            "training_rows": premium["training_data"]["rows"],
            "training_ranges": premium["training_data"]["ranges"],
            "numeric_clock_present_rows_NOT_validated_clock_semantics": premium["training_data"]["rows_with_any_valid_positive_clock"],
            "capture_time_verified_rows": premium["training_data"]["capture_time_verified_rows"],
            "training_event_date_classes": premium["training_data"]["event_date_classes"],
            "clock_slot_distance_mapping_validated": False,
            "speed_rows": premium["speed_figures"]["rows"],
            "speed_point_in_time_reconstruction_validated": False,
        },
        "priority_actions_NOT_executed": [
            {"priority": 1, "action_ja": "開催一覧と保存ID・全頭を照合し、不足レースと終了済み出馬表の結果を限定補完する。完了フラグに頭数・必須値の再検査を付ける。", "limit": "Expected IDs and cached calendar are not independent proof that every race was held; verify source before repair."},
            {"priority": 2, "action_ja": "稍/不・降着の表記正規化を共通生成経路で検証する。血統は整合する保存済み情報を再利用し、上がりは原HTMLの値と保存値を照合してから不足だけ取得する。", "limit": "No zero-fill, no invented clock, no blind overwrite; five cached examples are not a full-cache recovery estimate."},
            {"priority": 3, "action_ja": "調教時計のslot・距離・時点証拠を確認し、今後は入力とオッズを発走前時刻・原文hash付きで保存する。", "limit": "Late database registration is not proof of historical prestart publication; missingness repair is not evidence of profitability."},
        ],
        "external_primary_sources": [
            {"url": "https://www.jra.go.jp/company/about/outline/growth/kyoso/", "use": "Annual total JRA race count; not individual race-ID reconciliation."},
            {"url": "https://jra-van.jp/dlb/ddata.html", "use": "Official structured-data update schedule; per-field historical scope and licence require evaluation before adoption."},
        ],
        "protected_before_equals_after": not changed,
        "protected_files_before_equals_after": True,
        "protected_observed_changes": changed,
        "forward_process_observation": "The originally observed collector PIDs are no longer present. Cause unverified; this audit issued no stop/restart command. Do not claim uninterrupted forward collection.",
        "protected_after": protected_after,
        "actions": {"new_external_race_fetches": 0, "production_database_repairs": 0,
                    "training_started": False, "active_model_changed": False,
                    "h04_or_forward_changed": False, "restart": False, "commit_push": False},
    }
    output = directory / "quality_summary.json"
    manifest_path = directory / "artifact_manifest.json"
    if output.exists() or manifest_path.exists():
        raise FileExistsError("summary/manifest already sealed")
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    manifest = {
        "schema": "current-data-quality-artifact-manifest-v1", "snapshot_sha256": receipt["snapshot_sha256"],
        "files": {path.name: sha(path) for path in sorted(directory.glob("*.json"))},
        "audit_scripts": {str(path.relative_to(root)): sha(path) for path in [
            root / "scripts/audit_current_runner_quality.py", root / "scripts/audit_current_acquisition_coverage.py",
            root / "scripts/audit_current_learning_quality.py", root / "scripts/audit_current_cached_last3f.py",
            Path(__file__).resolve()]},
        "protected_before_equals_after": not changed,
        "protected_files_before_equals_after": True,
        "protected_observed_changes": changed,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"summary": str(output), "summary_sha256": sha(output),
                      "manifest_sha256": sha(manifest_path), "protected_before_equals_after": not changed,
                      "protected_files_before_equals_after": True,
                      "snapshot_body_unchanged": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
