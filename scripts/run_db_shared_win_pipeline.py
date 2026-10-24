"""Isolated fixed-time research capture, prediction, paper decision and settlement.

This is not the application purchase ledger or the H04 collector.  No model is
trained, activated, or published.  Capture is permitted only in the registered
window; later sources cannot repair a missed pre-race observation.  Every stage
is append-only and sealed.  A replay can never produce a Phase 1 PASS.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
for _root in (ROOT / "scripts", ROOT / "python-api", ROOT / "keiba"):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

from research_capture import prestart

SCHEMA = "db-shared-win-pipeline-v1"
PROOF_SCHEMA = "db-shared-win-pipeline-proof-v1"
DEFAULT_PLAN = ROOT / "reports/generated/db-shared-win-pipeline/20261012-kyoto11r-v2"
DEFAULT_PREPARED = ROOT / "reports/generated/db-shared-win-prestart/prepared/20261012T035756200445Z-98fcb0f66c3a"
DEFAULT_STUDY = ROOT / "reports/generated/db-shared-win-direct/20261012T034805317807Z-97e40af5"
DEFAULT_FRESHNESS = ROOT / "reports/generated/db-shared-history-freshness/20261012T043530309032Z-5e3e6035/audit.json"
TARGET = {"race_id": "202608040511", "race_date": "20261012", "venue": "京都",
          "surface": "turf", "distance": 1400, "expected_runner_count": 18,
          "post_time": "2026-10-12T06:45:00Z"}
POLICY = {"lead_seconds": 300, "tolerance_seconds": 60,
          "max_source_age_seconds": 300, "max_card_quote_skew_seconds": 60}
STUDY_PINS = {"manifest_sha256": "fc60379c42f3809a4b4644366ea2649f1832f6c783c0ae5b058f5da029ddfe0c",
              "results_sha256": "98e30b045e62640492a3715151e3271bf1c8206ad0865da7af97c765665248e7",
              "protocol_sha256": "c2f40a37991cdeb41f29ce3d90676bbea325c646da99faf3d743fa7bc8ef3334"}
_SOURCE_FILES = ("scripts/run_db_shared_win_pipeline.py", "scripts/capture_db_shared_win_prestart.py",
                 "scripts/verify_db_shared_win_prestart_inference.py", "python-api/research_capture/prestart.py",
                 "python-api/scraping/race.py", "python-api/scraping/odds.py",
                 "python-api/scraping/constants.py", "python-api/scraping/fetch_pipeline.py",
                 "scripts/evaluate_db_shared_venues.py")


class PipelineError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc)


def iso(value):
    return prestart._iso(value)


def instant(value):
    return prestart._instant(value)


def body(value):
    return prestart._body(value)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def file_hash(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(part)
    return hasher.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_bytes())


def source_pins():
    return {name: file_hash(ROOT / name) for name in _SOURCE_FILES}


def preservation():
    from evaluate_db_shared_venues import preservation_receipt
    return preservation_receipt()


def safe_plan(path):
    path = Path(path)
    boundary = (ROOT / "reports/generated/db-shared-win-pipeline").resolve()
    if (any(parent.is_symlink() for parent in (path, *path.parents))
            or not path.resolve().is_relative_to(boundary) or path.resolve() == boundary
            or path.name.endswith(".partial")):
        raise PipelineError("new_separated_research_plan_required")
    return path


def write_new(path, raw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def seal(directory, schema):
    files = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise PipelineError("stage_symlink_forbidden")
        if path.is_file():
            relative = path.relative_to(directory).as_posix()
            if relative == "artifact_manifest.json":
                raise PipelineError("stage_manifest_already_exists")
            files[relative] = {"sha256": file_hash(path), "bytes": path.stat().st_size}
    write_new(directory / "artifact_manifest.json", body({"schema": schema + "-manifest", "files": files}))


def verify_seal(directory, schema):
    directory = Path(directory)
    if directory.name.endswith(".partial") or any(p.is_symlink() for p in (directory, *directory.parents)):
        raise PipelineError("complete_nonsymlink_stage_required")
    manifest = read_json(directory / "artifact_manifest.json")
    if manifest.get("schema") != schema + "-manifest" or not manifest.get("files"):
        raise PipelineError("stage_manifest_invalid")
    actual = {p.relative_to(directory).as_posix() for p in directory.rglob("*")
              if p.is_file() and p.name != "artifact_manifest.json"}
    if actual != set(manifest["files"]):
        raise PipelineError("stage_has_unsealed_or_missing_objects")
    for name, record in manifest["files"].items():
        if Path(name).is_absolute() or ".." in Path(name).parts or "\\" in name:
            raise PipelineError("stage_object_path_invalid")
        path = directory / name
        if (path.is_symlink() or not path.resolve().is_relative_to(directory.resolve())
                or file_hash(path) != record["sha256"] or path.stat().st_size != record["bytes"]):
            raise PipelineError("stage_object_hash_mismatch")
    return file_hash(directory / "artifact_manifest.json")


@contextmanager
def exclusive(plan):
    path = safe_plan(plan) / ".pipeline.lock"
    descriptor = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        import psutil
        os.write(descriptor, body({"pid": os.getpid(), "pid_created_at_epoch": psutil.Process().create_time(),
                                   "started_at": iso(now())}))
        os.close(descriptor)
        descriptor = None
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        path.unlink()


def recover_lock(plan):
    """Remove only an orphaned local lock; never stop a live owner or resume HTTP."""
    import psutil
    checked = read_plan(plan)
    path = safe_plan(plan) / ".pipeline.lock"
    if not path.exists():
        return {"status": "no_orphan_lock", "network_requests": 0, "plan_sha256": checked["plan_sha256"]}
    raw = path.read_bytes()
    owner = json.loads(raw)
    if (type(owner.get("pid")) is not int or owner["pid"] <= 0
            or type(owner.get("pid_created_at_epoch")) not in (int, float)
            or not math.isfinite(owner["pid_created_at_epoch"]) or owner["pid_created_at_epoch"] <= 0):
        raise PipelineError("lock_owner_identity_unverified_no_automatic_release")
    try:
        running = psutil.Process(owner["pid"])
        live_owner = abs(running.create_time() - owner["pid_created_at_epoch"]) < 1e-3
    except psutil.NoSuchProcess:
        live_owner = False
    if live_owner:
        raise PipelineError("live_pipeline_owner_lock_must_not_be_released")
    if path.read_bytes() != raw:
        raise PipelineError("lock_changed_during_orphan_check")
    path.unlink()
    recovery = {"schema": SCHEMA + "-lock-recovery", "recovered_at": iso(now()),
                "old_lock_sha256": digest(raw), "old_pid": owner["pid"],
                "plan_sha256": checked["plan_sha256"], "owner_process_absent_or_pid_reused": True,
                "processes_stopped": 0, "network_requests": 0, "race_or_model_database_writes": 0,
                "interrupted_stage_objects_preserved": True}
    directory = Path(plan) / "recoveries" / (now().strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:10])
    directory.mkdir(parents=True)
    write_new(directory / "recovery.json", body(recovery))
    seal(directory, SCHEMA + "-lock-recovery")
    return {"status": "orphan_lock_recovered", "recovery_directory": str(directory.resolve()), **recovery}


def freshness_receipt(path):
    path = Path(path).resolve()
    manifest_path = path.parent / "artifact_manifest.json"
    manifest, audit = read_json(manifest_path), read_json(path)
    record = manifest.get("files", {}).get("audit.json", {})
    if (path.name != "audit.json" or path.is_symlink()
            or not path.is_relative_to((ROOT / "reports/generated/db-shared-history-freshness").resolve())
            or manifest.get("schema") != "db-shared-history-freshness-audit-v1"
            or audit.get("schema") != "db-shared-history-freshness-audit-v1"
            or record.get("sha256") != file_hash(path) or record.get("bytes") != path.stat().st_size
            or audit.get("cutoff_exclusive") != TARGET["race_date"]
            or audit.get("source_file_changed_during_read_transaction") is not False
            or audit.get("source_file_before") != audit.get("source_file_after")
            or audit.get("formula_changes") is not False):
        raise PipelineError("sealed_matching_day_history_freshness_audit_required")
    return {"path": str(path), "sha256": file_hash(path), "manifest_sha256": file_hash(manifest_path),
            "eligible_history_max_source_date": audit["eligible_complete_history_date_range"]["to"],
            "eligible_history_min_source_date": audit["eligible_complete_history_date_range"]["from"],
            "eligible_history_rows": audit["eligible_complete_history_runner_rows"],
            "eligible_history_races": audit["eligible_complete_history_races"],
            "eligible_identity_sha256": audit["eligible_identity_sha256"],
            "raw_feature_source_hashes": audit["source_hashes"],
            "history_freshness_complete": False, "current_career_coverage_verified": False,
            "gap_acknowledged_not_imputed": True,
            "source_database_sha256": audit["source_file_before"]["sha256"],
            "not_ev_or_buy_authority": True}


def bind_prepared_history(metadata, envelope, history_raw, fresh):
    """Compute the full identity comparison ONCE, before timed acquisition."""
    import pandas as pd
    history = pd.read_parquet(io.BytesIO(history_raw), columns=["race_id", "horse_number", "race_date"])
    state = envelope["state"]
    if (metadata.get("raw_feature_source_hashes") != fresh["raw_feature_source_hashes"]
            or metadata.get("history_rows") != fresh["eligible_history_rows"]
            or metadata.get("history_races") != fresh["eligible_history_races"]
            or metadata.get("history_min_source_date") != fresh["eligible_history_min_source_date"]
            or metadata.get("history_max_source_date") != fresh["eligible_history_max_source_date"]
            or len(history) != fresh["eligible_history_rows"] or history.race_id.nunique() != fresh["eligible_history_races"]
            or history.duplicated(["race_id", "horse_number"]).any()
            or history.race_date.min() != fresh["eligible_history_min_source_date"]
            or history.race_date.max() != fresh["eligible_history_max_source_date"]
            or not history.race_date.lt(TARGET["race_date"]).all()
            or metadata.get("history_parquet_sha256") != digest(history_raw)):
        raise PipelineError("prepared_history_cohort_counts_dates_or_formula_mismatch")
    identity = hashlib.sha256()
    for row in history.sort_values(["race_id", "horse_number"], kind="mergesort").itertuples(index=False, name=None):
        identity.update(json.dumps(row, separators=(",", ":")).encode() + b"\n")
    if identity.hexdigest() != fresh["eligible_identity_sha256"]:
        raise PipelineError("prepared_history_eligible_identity_set_different_from_freshness_audit")
    return {"schema": SCHEMA + "-prepared-history-binding", "computed_at_registration_once": True,
            "identity_columns": ["race_id", "horse_number", "race_date"],
            "identity_sha256": identity.hexdigest(), "rows": len(history), "races": int(history.race_id.nunique()),
            "history_date_from": history.race_date.min(), "history_date_to": history.race_date.max(),
            "history_parquet_sha256": digest(history_raw), "raw_feature_source_hashes": metadata["raw_feature_source_hashes"],
            "feature_source_hashes": metadata["feature_source_hashes"],
            "state_sha256": envelope["state_sha256"], "source_rows_sha256": state["source_rows_sha256"],
            "history_freshness_complete": False, "not_ev_or_buy_authority": True}


def verify_prepared_binding(plan_payload):
    """Cheap sealed metadata/binding validation; never parse 586k rows again."""
    prepared = Path(plan_payload["prepared_state"])
    manifest_path, metadata_path = prepared / "artifact_manifest.json", prepared / "metadata.json"
    if file_hash(manifest_path) != plan_payload["prepared_manifest_sha256"]:
        raise PipelineError("prepared_manifest_changed")
    manifest = read_json(manifest_path)
    entry = manifest.get("files", {}).get("metadata.json", {})
    if (file_hash(metadata_path) != entry.get("sha256") or metadata_path.stat().st_size != entry.get("bytes")
            or metadata_path.is_symlink()):
        raise PipelineError("prepared_metadata_hash_changed")
    metadata, binding = read_json(metadata_path), plan_payload["prepared_history_binding"]
    fresh = plan_payload["freshness_audit"]
    if (metadata.get("history_rows") != binding["rows"] or binding["rows"] != fresh["eligible_history_rows"]
            or metadata.get("history_races") != binding["races"] or binding["races"] != fresh["eligible_history_races"]
            or metadata.get("history_parquet_sha256") != binding["history_parquet_sha256"]
            or manifest.get("files", {}).get("history.parquet", {}).get("sha256") != binding["history_parquet_sha256"]
            or metadata.get("state_sha256") != binding["state_sha256"]
            or metadata.get("history_source_rows_sha256") != binding["source_rows_sha256"]
            or metadata.get("raw_feature_source_hashes") != binding["raw_feature_source_hashes"]
            or binding["raw_feature_source_hashes"] != fresh["raw_feature_source_hashes"]
            or metadata.get("feature_source_hashes") != binding["feature_source_hashes"]
            or binding["identity_sha256"] != fresh["eligible_identity_sha256"]
            or metadata.get("history_min_source_date") != binding["history_date_from"]
            or metadata.get("history_max_source_date") != binding["history_date_to"]):
        raise PipelineError("registered_prepared_cohort_binding_mismatch")
    return True


def register(plan, prepared=DEFAULT_PREPARED, study=DEFAULT_STUDY, freshness=DEFAULT_FRESHNESS):
    """Register the denominator and frozen decision rule BEFORE the window."""
    import verify_db_shared_win_prestart_inference as inference
    plan = safe_plan(plan)
    if plan.exists():
        return {"status": "already_registered", **read_plan(plan)}
    registered = now()
    if registered >= prestart.CapturePolicy(**POLICY).window(instant(TARGET["post_time"]))[0]:
        raise PipelineError("plan_must_be_registered_before_capture_window")
    study = Path(study).resolve()
    receipt = inference.verify_manifest(study)
    if any(receipt.get(key) != value for key, value in STUDY_PINS.items()):
        raise PipelineError("frozen_three_model_study_pin_mismatch")
    prepared = Path(prepared).resolve()
    metadata, envelope, history = prestart.read_prepared_state(prepared, TARGET["race_date"])
    fresh = freshness_receipt(freshness)
    if metadata["history_max_source_date"] != fresh["eligible_history_max_source_date"]:
        raise PipelineError("prepared_history_does_not_match_audited_latest_eligible_date")
    binding = bind_prepared_history(metadata, envelope, history, fresh)
    before = preservation()
    payload = {"schema": SCHEMA, "registered_at": iso(registered), "target": dict(TARGET),
        "capture_policy": dict(POLICY), "planned_races": 1, "planned_runner_count": 18,
        "prepared_state": str(prepared), "prepared_manifest_sha256": file_hash(prepared / "artifact_manifest.json"),
        "prepared_state_sha256": metadata["state_sha256"],
        "history_max_source_date": metadata["history_max_source_date"],
        "freshness_audit": fresh, "history_freshness_complete": False,
        "prepared_history_binding": binding,
        "history_gap_is_not_silently_repaired": True,
        "study_directory": str(study), "study_pins": dict(STUDY_PINS),
        "paper_rule": {"primary_candidate": "market46", "rule": "highest_calibrated_win_probability",
                       "tie_break": "lowest_horse_number", "bet_type": "tansho", "stake_yen": 100,
                       "fixed_before_outcome": True, "profit_selection": False},
        "source_hashes": source_pins(), "preservation_before": before,
        "research_only": True, "purchase_authorized": False, "actual_purchases": 0,
        "model_activation": False, "existing_forward_changed": False,
        "roi_scope": "one-race pipeline proof only; not statistical profitability evidence"}
    plan.parent.mkdir(parents=True, exist_ok=True)
    staged = plan.parent / (plan.name + "." + uuid.uuid4().hex[:12] + ".partial")
    staged.mkdir()
    write_new(staged / "plan.json", body(payload))
    seal(staged, SCHEMA)
    staged.rename(plan)
    return {"status": "registered", **read_plan(plan)}


def read_plan(plan):
    plan = safe_plan(plan)
    # The registration manifest covers only its immutable plan object.  Stage
    # directories are separately sealed and therefore not registration objects.
    manifest = read_json(plan / "artifact_manifest.json")
    if (manifest.get("schema") != SCHEMA + "-manifest" or set(manifest.get("files", {})) != {"plan.json"}
            or file_hash(plan / "plan.json") != manifest["files"]["plan.json"]["sha256"]
            or (plan / "plan.json").stat().st_size != manifest["files"]["plan.json"]["bytes"]):
        raise PipelineError("registered_plan_hash_mismatch")
    payload = read_json(plan / "plan.json")
    if (payload.get("schema") != SCHEMA or payload.get("target") != TARGET
            or payload.get("capture_policy") != POLICY or payload.get("planned_races") != 1
            or payload.get("planned_runner_count") != 18 or payload.get("study_pins") != STUDY_PINS
            or payload.get("research_only") is not True or payload.get("purchase_authorized") is not False
            or payload.get("source_hashes") != source_pins()):
        raise PipelineError("registered_protocol_or_source_mismatch")
    if instant(payload["registered_at"]) >= prestart.CapturePolicy(**POLICY).window(instant(TARGET["post_time"]))[0]:
        raise PipelineError("retroactive_plan_forbidden")
    verify_prepared_binding(payload)
    if freshness_receipt(payload["freshness_audit"]["path"]) != payload["freshness_audit"]:
        raise PipelineError("freshness_audit_changed")
    return {"plan_directory": str(plan.resolve()), "plan_sha256": file_hash(plan / "plan.json"), "plan": payload}


def stages(plan):
    checked = read_plan(plan)
    records = []
    for directory in sorted((Path(plan) / "stages").glob("*")):
        if not directory.is_dir() or directory.name.endswith(".partial"):
            continue
        manifest_sha = verify_seal(directory, SCHEMA + "-stage")
        receipt = read_json(directory / "stage.json")
        if receipt.get("plan_sha256") != checked["plan_sha256"] or receipt.get("schema") != SCHEMA + "-stage":
            raise PipelineError("stage_not_bound_to_plan")
        publication_path = Path(plan) / "publications" / manifest_sha
        if not publication_path.exists():
            # Power loss between stage rename and publication confirmation:
            # retain the sealed object but never grant it live authority.
            records.append({"directory": str(directory.resolve()), "manifest_sha256": manifest_sha,
                            "receipt": {**receipt, "unconfirmed_stage_status": receipt["status"],
                                        "status": "quarantined", "reason": "stage_publication_clock_unverified"},
                            "publication": None, "publication_manifest_sha256": None})
            continue
        publication_sha = verify_seal(publication_path, SCHEMA + "-publication")
        publication = read_json(publication_path / "publication.json")
        if publication.get("stage_manifest_sha256") != manifest_sha or publication.get("stage_directory") != str(directory.resolve()):
            raise PipelineError("stage_publication_binding_invalid")
        records.append({"directory": str(directory.resolve()), "manifest_sha256": manifest_sha, "receipt": receipt,
                        "publication": publication, "publication_manifest_sha256": publication_sha})
    return records


def _new_stage(plan, kind, parent=None):
    parent_dir = Path(plan) / "stages"
    parent_dir.mkdir(exist_ok=True)
    name = now().strftime("%Y%m%dT%H%M%S%fZ") + "-" + kind + "-" + uuid.uuid4().hex[:10]
    directory = parent_dir / (name + ".partial")
    directory.mkdir()
    receipt = {"schema": SCHEMA + "-stage", "stage": kind, "started_at": iso(now()),
               "plan_sha256": read_plan(plan)["plan_sha256"], "status": "started",
               "parent_manifest_sha256": parent, "research_only": True,
               "purchase_authorized": False, "actual_purchases": 0}
    return directory, receipt


def _publish_stage(directory, receipt):
    receipt["completed_at"] = iso(now())
    write_new(directory / "stage.json", body(receipt))
    seal(directory, SCHEMA + "-stage")
    final = directory.with_name(directory.name.removesuffix(".partial"))
    directory.rename(final)
    manifest_sha = verify_seal(final, SCHEMA + "-stage")
    publication = {"schema": SCHEMA + "-publication", "stage_directory": str(final.resolve()),
                   "stage_manifest_sha256": manifest_sha, "published_at": iso(now())}
    parent = final.parent.parent / "publications"
    parent.mkdir(exist_ok=True)
    staged = parent / (manifest_sha + ".partial")
    staged.mkdir()
    write_new(staged / "publication.json", body(publication))
    seal(staged, SCHEMA + "-publication")
    staged.rename(parent / manifest_sha)
    return {"directory": str(final.resolve()), "manifest_sha256": manifest_sha,
            "receipt": read_json(final / "stage.json"), "publication": publication,
            "publication_manifest_sha256": verify_seal(parent / manifest_sha, SCHEMA + "-publication")}


def select_paper_decision(predictions, target=TARGET, rule=None):
    """Diagnostic flat-stake paper decision, never an actual purchase or EV gate."""
    rule = rule or {"primary_candidate": "market46", "stake_yen": 100}
    rows = predictions.get(rule["primary_candidate"])
    if not isinstance(rows, list) or len(rows) != target["expected_runner_count"]:
        raise PipelineError("paper_whole_field_predictions_required")
    keys = [(str(row.get("horse_id", "")), int(row["horse_number"])) for row in rows]
    if (len({key[0] for key in keys}) != len(keys) or len({key[1] for key in keys}) != len(keys)
            or any(not horse for horse, _number in keys)
            or any(str(row.get("race_id")) != target["race_id"] or str(row.get("race_date")) != target["race_date"] for row in rows)):
        raise PipelineError("paper_runner_identity_mismatch")
    probabilities = [row.get("probability") for row in rows]
    if (any(isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 < p < 1 for p in probabilities)
            or abs(sum(probabilities) - 1) > 1e-10):
        raise PipelineError("paper_normalized_finite_probability_required")
    chosen = min(rows, key=lambda row: (-row["probability"], int(row["horse_number"])))
    price = chosen.get("odds")
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price < 1:
        raise PipelineError("paper_actual_quote_required")
    return {"race_id": target["race_id"], "race_date": target["race_date"],
            "candidate_id": rule["primary_candidate"], "horse_id": str(chosen["horse_id"]),
            "horse_number": int(chosen["horse_number"]), "probability": chosen["probability"],
            "observed_odds": price, "bet_type": "tansho", "stake_yen": rule["stake_yen"],
            "rule": "highest_calibrated_win_probability", "tie_break": "lowest_horse_number",
            "paper_only": True, "purchase_authorized": False, "actual_purchases": 0,
            "expected_value_authorized": False, "future_result_consumed": False}


def validate_capture(bundle, target=TARGET):
    info = bundle.get("card", {}).get("race_info", {})
    horses = bundle.get("card", {}).get("horses", [])
    if (bundle.get("mode") != "live" or bundle.get("race_id") != target["race_id"]
            or instant(bundle.get("post_time")) != instant(target["post_time"])
            or bundle.get("capture_validation", {}).get("point_in_time_verified") is not True
            or bundle.get("capture_validation", {}).get("reason_codes")
            or info.get("date") != target["race_date"] or info.get("venue") != target["venue"]
            or info.get("track_type") != "芝" or int(info.get("distance", 0)) != target["distance"]
            or int(info.get("num_horses", 0)) != target["expected_runner_count"]
            or len(horses) != target["expected_runner_count"]):
        raise PipelineError("fixed_target_complete_live_window_capture_required")
    keys = [(int(row.get("horse_number", 0)), str(row.get("horse_id") or "")) for row in horses]
    if (len({number for number, _horse in keys}) != len(keys) or len({horse for _number, horse in keys}) != len(keys)
            or any(not horse or not 1 <= number <= 18 for number, horse in keys)):
        raise PipelineError("capture_unique_runner_identities_required")
    policy = prestart.CapturePolicy(**POLICY)
    opens, deadline = policy.window(instant(target["post_time"]))
    if not opens <= instant(bundle["captured_at"]) <= deadline:
        raise PipelineError("capture_completed_outside_fixed_window")
    sources = bundle.get("source_receipts", [])
    if {item.get("kind") for item in sources} != {"card", "odds"} or len(sources) != 2:
        raise PipelineError("fresh_card_quote_sources_required")
    for source in sources:
        if (source.get("status") != 200 or source.get("source") != "network"
                or not opens <= instant(source["request_started_at"]) <= instant(source["observed_at"]) <= deadline):
            raise PipelineError("source_network_clock_window_unverified")
    return True


def verify_literal_quotes(bundle, capture_directory, target=TARGET):
    """Independently reparse the sealed API body; saved probability flags do not count."""
    from scraping.odds import parse_tansho_odds_payload
    receipts = [item for item in bundle.get("source_receipts", []) if item.get("kind") == "odds"]
    if len(receipts) != 1 or receipts[0].get("body_file") != "sources/odds.json":
        raise PipelineError("one_literal_api_win_odds_source_required")
    source = receipts[0]
    raw = (Path(capture_directory) / "sources/odds.json").read_bytes()
    if digest(raw) != source.get("body_sha256") or len(raw) != source.get("body_size"):
        raise PipelineError("literal_odds_api_body_hash_mismatch")
    try:
        payload = json.loads(raw)
        quotes, popularity, status = parse_tansho_odds_payload(payload, allow_predicted=False)
        data = payload["data"]
        raw_quotes = data["odds"]["1"]
        expected = {"yy": int(target["race_id"][:4]) % 100, "jyo": int(target["race_id"][4:6]),
                    "kai": int(target["race_id"][6:8]), "nichi": int(target["race_id"][8:10]),
                    "rno": int(target["race_id"][10:12])}
        for key, value in expected.items():
            actual = int(data[key])
            if key == "yy" and actual >= 2000:
                actual %= 100
            if actual != value:
                raise PipelineError("literal_odds_api_race_identity_mismatch")
        if int(data["shusso"]) != target["expected_runner_count"]:
            raise PipelineError("literal_odds_api_field_count_mismatch")
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        if isinstance(error, PipelineError):
            raise
        raise PipelineError("literal_odds_api_schema_or_identity_invalid") from error
    numbers = {int(row["horse_number"]) for row in bundle["card"]["horses"]}
    expected_quotes = {str(number): value for number, value in sorted(quotes.items())}
    if (status != "middle" or bundle.get("odds_status") != "middle" or not isinstance(raw_quotes, dict)
            or len(raw_quotes) != len(quotes) or set(quotes) != numbers
            or any(not math.isfinite(price) or price < 1 for price in quotes.values())
            or bundle.get("quotes") != expected_quotes):
        raise PipelineError("saved_quotes_do_not_equal_literal_prestart_api_body")
    for horse in bundle["card"]["horses"]:
        number = int(horse["horse_number"])
        if horse.get("odds") != quotes[number] or horse.get("popularity") != popularity.get(number):
            raise PipelineError("saved_card_quotes_or_popularity_not_literal_api_derived")
    extension = bundle.get("features_extended")
    if isinstance(extension, dict) and extension.get("market_source_body_sha256") != source["body_sha256"]:
        raise PipelineError("captured_matrix_not_bound_to_literal_api_body")
    return {"literal_api_body_sha256": source["body_sha256"], "exact_quote_value_parity": True,
            "exact_quote_runner_identity_parity": True, "exact_card_quote_popularity_parity": True,
            "parser_sha256": file_hash(ROOT / "python-api/scraping/odds.py"), "odds_status": status}


@contextmanager
def isolated_fetch_cache(plan):
    """Keep host request pacing, but put this job's disposable cache separately."""
    from unittest.mock import patch
    from scraping import fetch_pipeline
    cache = Path(plan) / "operational-http-cache.sqlite"
    with patch.object(fetch_pipeline, "_CACHE_DB_PATH", cache):
        try:
            yield
        finally:
            fetch_pipeline.close_fetch_cache_connections()


async def capture(plan, *, allow_network=False):
    if allow_network is not True:
        raise PipelineError("explicit_allow_network_required")
    import capture_db_shared_win_prestart as capture_cli
    import verify_db_shared_win_prestart_inference as inference
    checked = read_plan(plan)
    with exclusive(plan):
        done = [item for item in stages(plan) if item["receipt"]["stage"] == "capture" and item["receipt"]["status"] == "completed"]
        if done:
            if len(done) != 1:
                raise PipelineError("multiple_completed_captures_for_one_planned_race")
            verify_capture_stage(checked, done[0])
            return {"status": "already_captured", "capture_stage": done[0], "network_requests": 0}
        directory, receipt = _new_stage(plan, "capture")
        try:
            if preservation() != checked["plan"]["preservation_before"]:
                raise PipelineError("active_model_or_existing_forward_changed_before_capture")
            args = argparse.Namespace(action="capture", race_id=TARGET["race_id"], race_date=TARGET["race_date"],
                post_time=instant(TARGET["post_time"]), db_path=ROOT / "keiba/data/keiba_ultimate.db",
                store=Path(plan) / "capture-inputs", allow_network=True, outside_window_probe=False,
                prepared_state=Path(checked["plan"]["prepared_state"]), with_extended=True, **POLICY)
            with isolated_fetch_cache(plan):
                captured = await capture_cli.execute(args)
            receipt["capture_result"] = captured
            capture_path = Path(captured["path"])
            bundle = prestart.read_bundle(capture_path)
            validate_capture(bundle)
            literal_quote_receipt = verify_literal_quotes(bundle, capture_path)
            result = inference.run(Path(checked["plan"]["study_directory"]), capture_path, directory / "inference.json")
            completed = instant(result["completed_at"])
            if not instant(bundle["captured_at"]) <= completed < instant(TARGET["post_time"]):
                raise PipelineError("inference_completion_not_verified_before_post")
            decision = select_paper_decision(result["predictions"], rule=checked["plan"]["paper_rule"])
            decision["decision_created_at"] = iso(now())
            decision["prediction_sha256"] = result["prediction_sha256"]
            decision["model_input_sha256"] = result["input_receipt"]["model_input_sha256"]
            decision["capture_sha256"] = result["capture_sha256"]
            decision["market_source_body_sha256"] = result["input_receipt"]["market_source_body_sha256"]
            if not completed <= instant(decision["decision_created_at"]) < instant(TARGET["post_time"]):
                raise PipelineError("paper_decision_not_verified_before_post")
            write_new(directory / "paper-decision.json", body(decision))
            after = preservation()
            if after != checked["plan"]["preservation_before"]:
                raise PipelineError("active_model_or_existing_forward_changed_during_capture")
            receipt.update(status="completed", capture_directory=str(capture_path.resolve()),
                           capture_manifest_sha256=file_hash(capture_path / "artifact_manifest.json"),
                           prediction_sha256=result["prediction_sha256"], preservation_after=after,
                           capture_before_post_verified=True, prediction_before_post_verified=True,
                           decision_before_post_verified=True, decision_sha256=digest(body(decision)),
                           acquisition_routes=2, actual_network_request_count=None,
                           request_count_not_inferred_from_source_count=True, additional_inference_requests=0,
                           literal_quote_receipt=literal_quote_receipt)
        except Exception as error:
            receipt.update(status="failed", reason=str(error) if isinstance(error, (PipelineError, prestart.CaptureError))
                           else "pipeline_capture_failed_" + type(error).__name__)
        published = _publish_stage(directory, receipt)
        # The seal itself, not merely a decision timestamp, must exist pre-post.
        if receipt["status"] == "completed" and instant(published["publication"]["published_at"]) >= instant(TARGET["post_time"]):
            raise PipelineError("capture_stage_seal_not_before_post")
        return {"status": receipt["status"], "capture_stage": published}


def verify_capture_stage(checked, stage):
    import verify_db_shared_win_prestart_inference as inference
    record = stage["receipt"]
    directory = Path(stage["directory"])
    if record.get("status") != "completed" or record.get("stage") != "capture":
        raise PipelineError("completed_capture_stage_required")
    verify_seal(directory, SCHEMA + "-stage")
    captured = Path(record["capture_directory"])
    if not captured.resolve().is_relative_to(Path(checked["plan_directory"]) / "capture-inputs"):
        raise PipelineError("isolated_capture_source_required")
    bundle = prestart.read_bundle(captured)
    validate_capture(bundle)
    literal_quote_receipt = verify_literal_quotes(bundle, captured)
    if record.get("literal_quote_receipt") != literal_quote_receipt:
        raise PipelineError("literal_quote_readback_receipt_mismatch")
    binding = checked["plan"]["prepared_history_binding"]
    history_audit = bundle.get("history_audit", {})
    if (file_hash(captured / "sources/history.parquet") != binding["history_parquet_sha256"]
            or history_audit.get("prepared_state_sha256") != binding["state_sha256"]
            or history_audit.get("history_rows") != binding["rows"]
            or history_audit.get("history_races") != binding["races"]
            or history_audit.get("history_max_source_date") != binding["history_date_to"]):
        raise PipelineError("captured_history_different_from_registered_audited_cohort")
    if file_hash(captured / "artifact_manifest.json") != record["capture_manifest_sha256"]:
        raise PipelineError("capture_manifest_changed_after_prediction")
    prediction = read_json(directory / "inference.json")
    decision = read_json(directory / "paper-decision.json")
    study_receipt = inference.verify_manifest(Path(checked["plan"]["study_directory"]))
    if any(study_receipt.get(key) != value for key, value in STUDY_PINS.items()):
        raise PipelineError("study_changed_after_registration")
    frame, input_receipt = inference.frame_from_capture(bundle, captured)
    if (prediction.get("prediction_sha256") != digest(body(prediction["predictions"]))
            or prediction.get("prediction_sha256") != record.get("prediction_sha256")
            or prediction.get("capture_manifest_sha256") != record["capture_manifest_sha256"]
            or prediction.get("input_receipt") != input_receipt
            or prediction.get("protocol_sha256") != STUDY_PINS["protocol_sha256"]
            or prediction.get("serialized_model_replay_equal") is not True
            or prediction.get("current_outcomes_consumed") is not False):
        raise PipelineError("prediction_source_or_formula_binding_invalid")
    # This exact model replay is independent of stored stage flags.
    replay = inference.predict_all(inference.load_models(study_receipt), frame)
    if replay != prediction["predictions"]:
        raise PipelineError("sealed_predictions_do_not_replay_from_frozen_models")
    expected = select_paper_decision(prediction["predictions"], rule=checked["plan"]["paper_rule"])
    if any(decision.get(key) != value for key, value in expected.items()):
        raise PipelineError("paper_selection_not_fixed_rule")
    if (digest(body(decision)) != record.get("decision_sha256")
            or decision.get("prediction_sha256") != prediction["prediction_sha256"]
            or decision.get("model_input_sha256") != input_receipt["model_input_sha256"]
            or decision.get("capture_sha256") != prediction["capture_sha256"]
            or decision.get("market_source_body_sha256") != input_receipt["market_source_body_sha256"]):
        raise PipelineError("paper_decision_not_bound_to_observation")
    post = instant(TARGET["post_time"])
    if not (instant(bundle["captured_at"]) <= instant(prediction["completed_at"])
            <= instant(decision["decision_created_at"]) <= instant(record["completed_at"])
            <= instant(stage["publication"]["published_at"]) < post):
        raise PipelineError("capture_prediction_decision_clockchain_invalid")
    if instant(record["started_at"]) > min(instant(source["request_started_at"]) for source in bundle["source_receipts"]):
        raise PipelineError("capture_stage_started_after_source_request")
    if record.get("preservation_after") != checked["plan"]["preservation_before"]:
        raise PipelineError("capture_preservation_receipt_mismatch")
    return bundle, prediction, decision


def validate_result_response(source, raw, target=TARGET):
    """Transport/access failures are failures, not unpublished-result waiting."""
    from scraping.constants import is_cloudflare_block
    from scraping.fetch_pipeline import _looks_like_access_block
    from urllib.parse import parse_qs, urlsplit
    for name in ("url", "request_url"):
        parsed = urlsplit(str(source.get(name, "")))
        if (parsed.scheme != "https" or parsed.hostname != "race.netkeiba.com" or parsed.username or parsed.password
                or parsed.path != "/race/result.html" or parse_qs(parsed.query).get("race_id") != [target["race_id"]]):
            raise PipelineError("settlement_source_identity_mismatch")
    status = source.get("status")
    if type(status) is not int or status != 200:
        raise PipelineError("result_http_status_" + str(status) if type(status) is int else "result_http_status_unverified")
    if source.get("source") != "network":
        raise PipelineError("result_fresh_network_source_unverified")
    if digest(raw) != source.get("body_sha256") or len(raw) != source.get("body_size"):
        raise PipelineError("result_transport_body_hash_mismatch")
    if not raw or is_cloudflare_block(raw) or _looks_like_access_block(raw):
        raise PipelineError("result_access_blocked_or_empty_body")
    if not instant(target["post_time"]) <= instant(source["request_started_at"]) <= instant(source["observed_at"]):
        raise PipelineError("fresh_post_race_result_source_required")
    return True


def validate_result_page_identity(html, target=TARGET):
    from bs4 import BeautifulSoup
    from scraping import race
    soup = BeautifulSoup(html, "lxml")
    if race._verified_entry_card_date(soup, target["race_id"], target["race_date"]) != target["race_date"]:
        raise PipelineError("result_displayed_date_unverified")
    rd1 = soup.select_one(".RaceData01")
    found_time = re.search(r"(\d{1,2}:\d{2})\s*発走", rd1.get_text(" ", strip=True) if rd1 else "")
    if not found_time or found_time.group(1).zfill(5) != instant(target["post_time"]).astimezone(prestart.JST).strftime("%H:%M"):
        raise PipelineError("result_displayed_post_time_unverified")
    return soup


def normalize_settlement(html, snapshot, source, decision, bundle, target=TARGET):
    """Require source identity, full-field results, and literal actual win payout."""
    from scraping import race
    from urllib.parse import parse_qs, urlsplit
    for name in ("url", "request_url"):
        parsed = urlsplit(str(source.get(name, "")))
        if (parsed.scheme != "https" or parsed.hostname != "race.netkeiba.com" or parsed.username or parsed.password
                or parsed.path != "/race/result.html" or parse_qs(parsed.query).get("race_id") != [target["race_id"]]):
            raise PipelineError("settlement_source_identity_mismatch")
    if (source.get("status") != 200 or source.get("source") != "network"
            or not instant(target["post_time"]) <= instant(source["request_started_at"]) <= instant(source["observed_at"])):
        raise PipelineError("fresh_post_race_result_source_required")
    soup = validate_result_page_identity(html, target)
    parsed = race.parse_current_result_snapshot(html, target["race_id"])
    if parsed is None:
        raise PipelineError("result_or_payout_not_yet_available")
    if snapshot != parsed:
        raise PipelineError("result_actual_parser_mismatch")
    table = soup.select_one("table.RaceTable01")
    entries = []
    for row in table.select("tr"):
        cells = row.find_all("td", recursive=False)
        if not cells:
            continue
        if len(cells) < 4:
            raise PipelineError("result_partial_row")
        number = cells[2].get_text(strip=True)
        position = cells[0].get_text(strip=True)
        if not number.isdigit() or not position.isdigit():
            raise PipelineError("result_cancellation_or_nonfinish_requires_refund_verification")
        anchor = row.select_one('a[href*="/horse/"]')
        horse = re.search(r"/horse/(\d+)(?:/|$)", urlsplit(str(anchor.get("href", "")) if anchor else "").path)
        if not horse:
            raise PipelineError("result_horse_identity_unverified")
        entries.append({"horse_number": int(number), "horse_id": horse.group(1), "finish": int(position)})
    card_keys = {(int(row["horse_number"]), str(row["horse_id"])) for row in bundle["card"]["horses"]}
    result_keys = {(row["horse_number"], row["horse_id"]) for row in entries}
    if (len(entries) != target["expected_runner_count"] or len(result_keys) != len(entries)
            or result_keys != card_keys or len(parsed["horses"]) != len(entries)
            or any(row["finish"] <= 0 or row["finish"] > len(entries) for row in entries)):
        raise PipelineError("complete_result_card_identity_match_required")
    if len({row["finish"] for row in entries}) != len(entries):
        raise PipelineError("dead_heat_outcome_requires_separate_protocol")
    winners = [row for row in entries if row["finish"] == 1]
    payouts = [row for row in parsed["payouts"] if row.get("bet_type") == "tansho"]
    if len(winners) != 1 or len(payouts) != 1:
        raise PipelineError("single_winner_actual_win_payout_required")
    payout = payouts[0]
    literal_payouts = soup.select("table.Payout_Detail_Table tr.Tansho td.Payout span")
    if len(literal_payouts) != 1:
        raise PipelineError("single_literal_win_payout_token_required")
    payout_text = re.sub(r"\s+", "", literal_payouts[0].get_text("", strip=True))
    if re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)円?", payout_text) is None:
        raise PipelineError("actual_win_payout_literal_token_invalid")
    literal_payout = int(payout_text.removesuffix("円").replace(",", ""))
    if (str(payout.get("combination")) != str(winners[0]["horse_number"])
            or type(payout.get("payout")) is not int or payout["payout"] != literal_payout
            or payout["payout"] < 100 or payout["payout"] % 10 != 0):
        raise PipelineError("actual_win_payout_winner_mismatch")
    hit = decision["horse_number"] == winners[0]["horse_number"]
    returned = payout["payout"] if hit else 0
    return {"race_id": target["race_id"], "race_date": target["race_date"], "runner_results": entries,
            "winner": winners[0], "actual_tansho_payout_per_100_yen": payout["payout"],
            "paper_stake_yen": 100, "paper_return_yen": returned, "paper_profit_yen": returned - 100,
            "paper_hit": hit, "paper_roi_percent": float(returned), "decision_sha256": digest(body(decision)),
            "result_source_body_sha256": source["body_sha256"], "payout_source": "literal_current_result_win_payout_table",
            "settlement_from_final_odds": False, "paper_only": True, "actual_purchases": 0,
            "result_race_identity_verified": True, "result_runner_identity_verified": True,
            "actual_settlement_verified": True, "roi_is_pipeline_smoke_test_not_profit_evidence": True}


async def acquire_result(plan, target=TARGET):
    """One result-only fetch route; never enrich horse profiles or write race DB."""
    import aiohttp
    from scraping import race
    from unittest.mock import patch
    original = race.fetch_text
    observed = []

    async def observer(session, url, **kwargs):
        started = now()
        response, html = await original(session, url, **kwargs)
        ended = now()
        observed.append({"request_url": url, "url": response.url, "status": response.status,
                         "source": response.source, "request_started_at": iso(started), "observed_at": iso(ended),
                         "body_sha256": digest(bytes(response.body)), "body_size": len(response.body),
                         "body": bytes(response.body), "html": html,
                         "server_date": next((v for k, v in response.response_headers.items() if k.lower() == "date"), None)})
        return response, html

    with isolated_fetch_cache(plan), patch.object(race, "fetch_text", observer):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90), headers={"User-Agent": "Mozilla/5.0"}) as session:
            snapshot = await race.scrape_current_race_result(session, target["race_id"], force_refresh=True)
    if len(observed) != 1:
        raise PipelineError("one_result_only_source_required")
    return snapshot, observed[0]


async def settle(plan, *, allow_network=False):
    if allow_network is not True:
        raise PipelineError("explicit_allow_network_required")
    checked = read_plan(plan)
    with exclusive(plan):
        observations = stages(plan)
        settled = [item for item in observations if item["receipt"]["stage"] == "settlement" and item["receipt"]["status"] == "completed"]
        if settled:
            proof = verify(plan)
            return {"status": "already_settled", "proof": proof, "network_requests": 0}
        captures = [item for item in observations if item["receipt"]["stage"] == "capture" and item["receipt"]["status"] == "completed"]
        if len(captures) != 1:
            raise PipelineError("one_verified_prerace_capture_required_before_outcome_access")
        bundle, _prediction, decision = verify_capture_stage(checked, captures[0])
        if now() <= instant(TARGET["post_time"]):
            raise PipelineError("result_access_not_permitted_before_post")
        directory, receipt = _new_stage(plan, "settlement", captures[0]["manifest_sha256"])
        try:
            if preservation() != checked["plan"]["preservation_before"]:
                raise PipelineError("active_model_or_existing_forward_changed_before_settlement")
            snapshot, response = await acquire_result(plan)
            raw, html = response.pop("body"), response.pop("html")
            write_new(directory / "result.html", raw)
            write_new(directory / "result-source.json", body(response))
            receipt["source_http_status"] = response.get("status")
            receipt["source_kind"] = response.get("source")
            validate_result_response(response, raw)
            validate_result_page_identity(html)
            if snapshot is None:
                raise PipelineError("result_or_payout_not_yet_available")
            settlement = normalize_settlement(html, snapshot, response, decision, bundle)
            write_new(directory / "settlement.json", body(settlement))
            after = preservation()
            if after != checked["plan"]["preservation_before"]:
                raise PipelineError("active_model_or_existing_forward_changed_during_settlement")
            receipt.update(status="completed", preservation_after=after, actual_settlement_verified=True,
                           settlement_sha256=digest(body(settlement)), source_body_sha256=response["body_sha256"],
                           source_observed_at=response["observed_at"], acquisition_routes=1)
        except Exception as error:
            pending_reasons = {"result_or_payout_not_yet_available",
                               "result_cancellation_or_nonfinish_requires_refund_verification",
                               "dead_heat_outcome_requires_separate_protocol"}
            receipt.update(status="pending" if isinstance(error, PipelineError) and str(error) in pending_reasons else "failed",
                           reason=str(error) if isinstance(error, PipelineError) else "pipeline_settlement_failed_" + type(error).__name__)
        published = _publish_stage(directory, receipt)
        result = {"status": receipt["status"], "settlement_stage": published}
        if receipt["status"] == "completed":
            result["proof"] = verify(plan, publish=True)
        return result


def _proof_payload(plan):
    from scraping.fetch_pipeline import decode_html_body
    from scraping import race
    checked = read_plan(plan)
    observations = stages(plan)
    captures = [item for item in observations if item["receipt"]["stage"] == "capture" and item["receipt"]["status"] == "completed"]
    settlements = [item for item in observations if item["receipt"]["stage"] == "settlement" and item["receipt"]["status"] == "completed"]
    counters = {"planned_races": 1, "completed_captures": len(captures), "completed_settlements": len(settlements),
                "missing_planned_capture_races": int(not captures), "missing_planned_settlement_races": int(not settlements),
                "skipped_planned_races": 0,
                "failed_capture_attempts": sum(row["receipt"]["stage"] == "capture" and row["receipt"]["status"] == "failed" for row in observations),
                "pending_settlement_attempts": sum(row["receipt"]["stage"] == "settlement" and row["receipt"]["status"] == "pending" for row in observations),
                "failed_settlement_attempts": sum(row["receipt"]["stage"] == "settlement" and row["receipt"]["status"] == "failed" for row in observations),
                "quarantined_publication_attempts": sum(row["receipt"]["status"] == "quarantined" for row in observations),
                "partial_stage_count": sum(path.name.endswith(".partial") for path in (Path(plan) / "stages").glob("*"))}
    if len(captures) != 1 or len(settlements) != 1:
        return {"schema": PROOF_SCHEMA, "status": "incomplete", "phase1_pass": False,
                "plan_directory": str(Path(plan).resolve()), "plan_sha256": checked["plan_sha256"],
                **counters,
                "research_only": True, "purchase_authorized": False, "actual_purchases": 0}
    bundle, prediction, decision = verify_capture_stage(checked, captures[0])
    final = settlements[0]
    record, directory = final["receipt"], Path(final["directory"])
    if record.get("parent_manifest_sha256") != captures[0]["manifest_sha256"]:
        raise PipelineError("settlement_capture_chain_mismatch")
    source = read_json(directory / "result-source.json")
    raw = (directory / "result.html").read_bytes()
    if digest(raw) != source.get("body_sha256") or len(raw) != source.get("body_size"):
        raise PipelineError("settlement_literal_body_hash_mismatch")
    validate_result_response(source, raw)
    html = decode_html_body(raw)
    outcome = normalize_settlement(html, race.parse_current_result_snapshot(html, TARGET["race_id"]), source, decision, bundle)
    if outcome != read_json(directory / "settlement.json") or digest(body(outcome)) != record.get("settlement_sha256"):
        raise PipelineError("literal_result_and_saved_settlement_mismatch")
    if (record.get("preservation_after") != checked["plan"]["preservation_before"]
            or not instant(decision["decision_created_at"]) < instant(TARGET["post_time"])
            <= instant(record["started_at"]) <= instant(source["request_started_at"])
            <= instant(source["observed_at"]) <= instant(record["completed_at"])):
        raise PipelineError("settlement_clockchain_or_preservation_invalid")
    if preservation() != checked["plan"]["preservation_before"]:
        raise PipelineError("active_model_or_existing_forward_currently_changed")
    return {"schema": PROOF_SCHEMA, "status": "completed", "phase1_pass": True,
            "plan_directory": str(Path(plan).resolve()), "plan_sha256": checked["plan_sha256"],
            "capture_stage_manifest_sha256": captures[0]["manifest_sha256"],
            "settlement_stage_manifest_sha256": final["manifest_sha256"],
            "capture_publication_manifest_sha256": captures[0]["publication_manifest_sha256"],
            "settlement_publication_manifest_sha256": final["publication_manifest_sha256"],
            "capture_before_post_verified": True, "prediction_before_post_verified": True,
            "decision_before_post_verified": True, "actual_settlement_verified": True,
            "capture_mode": bundle["mode"], "race_id": TARGET["race_id"], "race_date": TARGET["race_date"],
            "post_time": TARGET["post_time"], "captured_at": bundle["captured_at"],
            "prediction_completed_at": prediction["completed_at"], "decision_created_at": decision["decision_created_at"],
            "capture_stage_published_at": captures[0]["publication"]["published_at"],
            "result_observed_at": source["observed_at"], "settlement_completed_at": record["completed_at"],
            "planned_races": 1, "planned_runner_count": 18, "settled_races": 1,
            **counters,
            "prediction_sha256": prediction["prediction_sha256"], "paper_decision": decision,
            "paper_settlement": outcome, "source_hashes": checked["plan"]["source_hashes"],
            "preservation_before": checked["plan"]["preservation_before"], "preservation_after": record["preservation_after"],
            "prediction_accuracy_validation_completed": False, "calibration_statistical_validation_completed": False,
            "roi_statistical_validation_completed": False, "research_only": True,
            "history_freshness_complete": False, "current_career_coverage_verified": False,
            "freshness_audit": checked["plan"]["freshness_audit"],
            "prepared_history_binding": checked["plan"]["prepared_history_binding"],
            "literal_quotes_reparsed_verified": True,
            "literal_quote_receipt": captures[0]["receipt"]["literal_quote_receipt"],
            "operational_input_complete_verified": False, "expected_value_authorized": False,
            "purchase_authorized": False, "actual_purchases": 0, "existing_forward_changed": False,
            "use_as_live_profit_claim": False, "four_candidate_training_now_eligible": True}


def verify(plan, *, publish=False):
    """Recompute all clock, formula, prediction, source and settlement proofs."""
    result = _proof_payload(plan)
    proof_path = Path(plan) / "proof.json"
    manifest_path = Path(plan) / "proof-manifest.json"
    if proof_path.exists():
        if read_json(proof_path) != result:
            raise PipelineError("published_proof_does_not_recompute")
    elif publish and result.get("phase1_pass") is True:
        write_new(proof_path, body(result))
    if proof_path.exists():
        expected = {"schema": PROOF_SCHEMA + "-manifest", "proof_sha256": file_hash(proof_path),
                    "proof_bytes": proof_path.stat().st_size, "plan_sha256": result["plan_sha256"],
                    "capture_stage_manifest_sha256": result["capture_stage_manifest_sha256"],
                    "settlement_stage_manifest_sha256": result["settlement_stage_manifest_sha256"]}
        if manifest_path.exists():
            if read_json(manifest_path) != expected:
                raise PipelineError("published_proof_manifest_mismatch")
        elif publish:
            write_new(manifest_path, body(expected))
    return result


def verify_gate_proof(proof_path):
    """Phase 2 must call this, not trust JSON true flags or a hash alone."""
    proof_path = Path(proof_path)
    if proof_path.name != "proof.json" or not proof_path.is_file() or not (proof_path.parent / "proof-manifest.json").is_file():
        raise PipelineError("sealed_phase1_gate_proof_required")
    result = verify(proof_path.parent)
    if result.get("phase1_pass") is not True or result.get("status") != "completed":
        raise PipelineError("actual_live_capture_to_settlement_not_complete")
    return {"proof": result, "proof_sha256": file_hash(proof_path),
            "proof_manifest_sha256": file_hash(proof_path.parent / "proof-manifest.json")}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("register", "capture", "settle", "verify", "recover-lock"))
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--prepared-state", type=Path, default=DEFAULT_PREPARED)
    parser.add_argument("--study", type=Path, default=DEFAULT_STUDY)
    parser.add_argument("--freshness-audit", type=Path, default=DEFAULT_FRESHNESS)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--publish-proof", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.action == "register":
            result = register(args.plan, args.prepared_state, args.study, args.freshness_audit)
        elif args.action == "capture":
            result = asyncio.run(capture(args.plan, allow_network=args.allow_network))
        elif args.action == "settle":
            result = asyncio.run(settle(args.plan, allow_network=args.allow_network))
        elif args.action == "recover-lock":
            result = recover_lock(args.plan)
        else:
            result = verify(args.plan, publish=args.publish_proof)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0 if result.get("status") in {"registered", "already_registered", "completed", "already_captured", "already_settled", "no_orphan_lock", "orphan_lock_recovered"} else 3
    except Exception as error:
        reason = str(error) if isinstance(error, (PipelineError, prestart.CaptureError)) else "pipeline_failed_" + type(error).__name__
        print(json.dumps({"status": "rejected", "reason": reason, "research_only": True,
                          "purchase_authorized": False, "actual_purchases": 0}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
