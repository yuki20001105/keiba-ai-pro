"""Independent Phase-1 release gate. Reads sealed audits; writes outside them."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

import numpy as np
import pandas as pd


REPO = Path(__file__).resolve().parents[1]


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path):
    return json.loads(Path(path).read_text("utf-8"))


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _bound(root, name):
    path = root / name
    _require(not Path(name).is_absolute() and not path.is_symlink(), "manifest path is not relative/regular")
    resolved = path.resolve(strict=True)
    _require(resolved.is_relative_to(root.resolve()) and resolved.is_file(), "manifest path escapes root")
    return resolved


def _bool(values, label):
    _require(values.notna().all(), label + " has missing boolean values")
    mapping = {"True": True, "False": False}
    text = values.astype(str)
    _require(text.isin(mapping).all(), label + " has nonboolean values")
    return text.map(mapping).astype(bool)


def _csv(path):
    return pd.read_csv(path, dtype={"race_id": str, "race_date": str, "horse_id": str})


def _race_index(frame, label):
    _require("race_id" in frame and frame.race_id.notna().all() and not frame.race_id.duplicated().any(),
             label + " has invalid/duplicate race keys")
    return frame.set_index("race_id").sort_index()


def _keys(frame):
    _require({"race_id", "horse_number", "race_date", "horse_id"} <= set(frame), "runner identity columns missing")
    _require(frame[["race_id", "horse_number", "horse_id"]].notna().all().all(), "runner key missing")
    numbers = pd.to_numeric(frame.horse_number, errors="coerce")
    _require((np.isfinite(numbers) & numbers.between(1, 18) & numbers.mod(1).eq(0)).all(), "runner number invalid")
    _require(frame.race_date.astype(str).str.fullmatch(r"202[12]\d{4}").all(), "runner date outside 2021/2022")
    pd.to_datetime(frame.race_date, format="%Y%m%d", errors="raise")
    result = frame.copy()
    result["horse_number"] = numbers.astype(int)
    _require(not result.duplicated(["race_id", "horse_number"]).any(), "duplicate runner key")
    return result.set_index(["race_id", "horse_number"]).sort_index()


def _verify(quality, worker, repo, receipt):
    manifest_path = quality / "artifact_manifest.json"
    summary_path = quality / "quality_summary.json"
    manifest, summary = _json(manifest_path), _json(summary_path)
    _require(manifest.get("schema") == "market-h04-quality-artifacts-v1", "wrong quality manifest schema")
    required = {"quality_summary.json", "audit_plan.json", "lineage.json", "clean_runners.parquet", "clean_races.csv",
                "excluded_races.csv", "race_quality_audit.csv", "runner_integrity.csv", "payout_audit.csv",
                "odds_quality.csv", "acquisition/acquisition_audit.csv"}
    actual_files = {str(p.relative_to(quality)).replace("\\", "/") for p in quality.rglob("*") if p.is_file()}
    declared = {name.replace("\\", "/") for name in manifest["files"]}
    _require(required <= declared and len(declared) == len(manifest["files"]), "quality manifest lacks required evidence")
    _require(actual_files == declared | {"artifact_manifest.json"}, "sealed quality directory has missing/unlisted files")
    for group, root in (("files", quality), ("code", repo)):
        _require(bool(manifest[group]), "empty manifest " + group)
        for name, expected in manifest[group].items():
            _require(re.fullmatch(r"[0-9a-f]{64}", str(expected)) is not None, "invalid manifest hash")
            _require(sha256(_bound(root, name)) == expected, "manifest hash mismatch: " + name)
    receipt.update(quality_summary_sha256=sha256(summary_path), quality_manifest_sha256=sha256(manifest_path))
    receipt["checks"]["quality_files_and_code_hashes"] = True
    _require(summary.get("MODEL_RESEARCH_READY") is True and not summary.get("blockers"), "quality gate is not PASS")
    _require(bool(summary.get("gate_checks")) and all(v is True for v in summary["gate_checks"].values()), "quality component gate failed")
    plan, lineage = _json(quality / "audit_plan.json"), _json(quality / "lineage.json")
    plan_hash, lineage_hash = sha256(quality / "audit_plan.json"), sha256(quality / "lineage.json")
    _require(summary.get("audit_plan_sha256") == plan_hash == lineage.get("audit_plan_sha256"), "quality/lineage plan mismatch")
    _require(sha256(worker / "audit_plan.json") == plan_hash, "worker frozen plan mismatch")
    _require(lineage.get("source_snapshot_sha256") == plan.get("snapshot_sha256"), "lineage snapshot mismatch")
    result_path = worker / "lineage-job-result.json"
    result, result_hash = _json(result_path), sha256(result_path)
    _require(result.get("status") == "completed" and result.get("training_performed") is False,
             "worker result incomplete or training performed")
    _require(result.get("lineage_and_event_time_ready") is True and result.get("lineage_sha256") == lineage_hash,
             "worker result does not bind successful lineage")
    # v2 may copy the exact v1 lineage receipt; verify both original and copied bytes.
    original_lineage = Path(result["lineage_path"]).resolve(strict=True)
    _require(original_lineage.is_relative_to(repo) and sha256(original_lineage) == lineage_hash,
             "worker's original lineage receipt differs")
    ledger_path = worker / "job_ledger.sqlite"
    with sqlite3.connect(ledger_path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        try:
            stored_plan = conn.execute("SELECT value FROM meta WHERE key='plan_sha256'").fetchone()
            jobs = [dict(row) for row in conn.execute("SELECT * FROM jobs")]
        finally:
            conn.rollback()
    _require(stored_plan is not None and stored_plan[0] == plan_hash, "ledger plan mismatch")
    _require(len(jobs) == 1 and jobs[0]["job_id"] == "lineage-replay", "unexpected lineage job ledger scope")
    job = jobs[0]
    _require(job["status"] == "completed" and job["exit_code"] == 0 and job["result_sha256"] == result_hash,
             "ledger completion/result hash mismatch")
    _require(Path(job["result_path"]).resolve() == result_path.resolve(), "ledger result path mismatch")
    command = json.loads(job["command_json"])
    for option, expected in (("--plan", str((worker / "audit_plan.json").resolve())),
                             ("--plan-sha256", plan_hash), ("--result", str(result_path.resolve()))):
        _require(command.count(option) == 1 and command.index(option) + 1 < len(command), "worker command option missing")
        actual = command[command.index(option) + 1]
        _require((Path(actual).resolve() == Path(expected).resolve()) if option != "--plan-sha256" else actual == expected,
                 "worker command binding mismatch: " + option)
    receipt.update(audit_plan_sha256=plan_hash, lineage_sha256=lineage_hash, worker_result_sha256=result_hash,
                   worker_ledger_path=str(ledger_path), worker_completed_job_id=job["job_id"])
    receipt["checks"]["worker_plan_lineage_and_ledger_binding"] = True
    features, pit = lineage.get("feature_columns", []), lineage.get("point_in_time") or {}
    entries = pit.get("per_feature", [])
    _require(len(features) == len(set(features)) == 99 and lineage.get("feature_count") == 99
             and len(entries) == pit.get("features_checked") == 99
             and {entry["feature"] for entry in entries} == set(features), "exact frozen 99-feature evidence missing")
    _require(all(entry["mismatched_rows"] == 0 and entry.get("max_absolute_difference") in (None, 0)
                 for entry in entries), "nonzero feature replay differences")
    _require(pit.get("actual_event_replay") is True and pit.get("mismatched_feature_rows") == 0
             and pit.get("same_day_future_references") == 0 and pit.get("future_leakage_rows") == 0
             and all(lineage.get(k) is True for k in ("lineage_pass", "point_in_time_pass", "reinference_pass", "lineage_and_event_time_ready")),
             "lineage/PIT gate lacks proof")
    replays = lineage.get("reinference", [])
    _require(len(replays) == 2 and {r["year"] for r in replays} == {2021, 2022}
             and all(r.get("pass") is True and r["mismatched_scores"] == 0
                     and r["max_absolute_score_difference"] <= plan["prediction_replay_tolerance"] for r in replays),
             "both fixed OOT replay years must pass")
    receipt["checks"]["exact_99_feature_and_two_year_replay"] = True
    original_path = repo / "reports/generated/model-experiments/market-edge-v1/development.parquet"
    _require(sha256(original_path) == plan["original_cohort_sha256"], "original cohort hash changed")
    original = pd.read_parquet(original_path, filters=[("race_date", "<=", "20221231")], use_threads=False)
    clean_path = quality / "clean_runners.parquet"
    _require(sha256(clean_path) == summary["clean_runners_sha256"]
             and sha256(quality / "clean_races.csv") == summary["clean_races_sha256"], "summary clean artifact hash mismatch")
    clean = pd.read_parquet(clean_path, use_threads=False)
    original_keys, clean_keys = _keys(original), _keys(clean)
    original_ids = set(original.race_id)
    _require(len(original) == plan["scope"]["original_runners"] == summary["original_runners"]
             and len(original_ids) == plan["scope"]["original_races"] == summary["original_races"], "original cohort counts differ")
    all_races = _race_index(_csv(quality / "race_quality_audit.csv"), "all race audit")
    kept = _race_index(_csv(quality / "clean_races.csv"), "clean race audit")
    excluded = _race_index(_csv(quality / "excluded_races.csv"), "excluded race audit")
    _require(set(all_races.index) == original_ids and not set(kept.index) & set(excluded.index)
             and set(kept.index) | set(excluded.index) == original_ids, "clean/excluded race partition mismatch")
    expected = pd.Series(True, index=all_races.index)
    for filename, flag in (("runner_integrity.csv", "runner_pass"), ("payout_audit.csv", "payout_pass"),
                           ("acquisition/acquisition_audit.csv", "acquisition_pass")):
        part = _race_index(_csv(quality / filename), filename)
        _require(set(part.index) == original_ids, "component race set mismatch: " + filename)
        authoritative = _bool(part[flag], filename).reindex(all_races.index)
        _require(authoritative.equals(_bool(all_races[flag], "combined " + flag)), "combined component flag mismatch")
        expected &= authoritative
    expected &= ~_bool(all_races.excluded_from_standard, "excluded_from_standard")
    odds = pd.read_csv(quality / "odds_quality.csv", dtype={"race_id": str}, usecols=[
        "race_id", "horse_number", "odds_quality", "invalid_odds", "market_probability_mismatch",
        "raw_implied_mismatch", "normalization_unavailable"])
    _require(not odds.duplicated(["race_id", "horse_number"]).any(), "duplicate odds audit key")
    odds_keys = pd.MultiIndex.from_frame(odds[["race_id", "horse_number"]]).sort_values()
    _require(odds_keys.equals(original_keys.index), "odds audit runner set mismatch")
    odds_fail = ~odds.odds_quality.isin(["A", "B", "C"])
    for column in ("invalid_odds", "market_probability_mismatch", "raw_implied_mismatch", "normalization_unavailable"):
        odds_fail |= _bool(odds[column], "odds " + column)
    expected &= ~odds_fail.groupby(odds.race_id).any().reindex(expected.index)
    actual_kept = _bool(all_races.included_in_clean, "included_in_clean")
    _require(expected.equals(actual_kept) and set(all_races.index[expected]) == set(kept.index),
             "clean cohort does not match independent race eligibility")
    _require(set(all_races.index[~expected]) == set(excluded.index)
             and kept.quality_class.str.startswith(("A ", "B ")).all(), "clean class/partition mismatch")
    _require(len(kept) == summary["clean_races"] > 0 and len(excluded) == summary["excluded_races"]
             and len(clean) == summary["clean_runners"], "summary clean/excluded counts mismatch")
    expected_clean = original_keys.loc[original_keys.index.get_level_values("race_id").isin(kept.index)]
    pd.testing.assert_frame_equal(clean_keys, expected_clean, check_exact=True, check_dtype=True)
    receipt["checks"]["clean_keys_values_and_whole_race_eligibility"] = True
    receipt["cohort"] = {"races": len(kept), "runners": len(clean), "excluded_races": len(excluded)}
    _require(sha256(manifest_path) == receipt["quality_manifest_sha256"], "quality manifest changed during verification")


def verify_release(quality_root, worker_root, repo_root=REPO):
    """Return a fail-closed receipt without writing to any quality artifact."""
    quality, worker, repo = (Path(path).resolve() for path in (quality_root, worker_root, repo_root))
    receipt = {"schema": "market-h04-quality-release-v1", "released": False, "quality_root": str(quality),
               "created_at": datetime.now(timezone.utc).isoformat(), "checks": {}, "issues": [],
               "quality_summary_sha256": None, "quality_manifest_sha256": None,
               "source_script_sha256": sha256(Path(__file__)), "new_training_performed": False}
    try:
        _require(quality.is_relative_to(repo) and worker.is_relative_to(repo) and not worker.is_relative_to(quality),
                 "quality/worker paths must be separate in repository")
        _verify(quality, worker, repo, receipt)
    except (ValueError, KeyError, TypeError, OSError, AssertionError, sqlite3.Error) as exc:
        receipt["issues"].append(type(exc).__name__ + ": " + str(exc))
    else:
        receipt["released"] = True
    return receipt


def save_release(receipt, worker_root):
    path = Path(worker_root).resolve() / "quality-release.json"
    _require(not path.is_relative_to(Path(receipt["quality_root"]).resolve()), "release must be outside sealed quality root")
    with path.open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quality-root", type=Path, required=True)
    parser.add_argument("--worker-root", type=Path, default=REPO / "reports/generated/model-experiments/market-quality-audit-v1")
    args = parser.parse_args()
    receipt = verify_release(args.quality_root, args.worker_root)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    save_release(receipt, args.worker_root)
    if not receipt["released"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
