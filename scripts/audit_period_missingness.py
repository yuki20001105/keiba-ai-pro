"""Audit existing 2016-present raw data and the frozen research feature cohort."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba")]

import pandas as pd

from experiments.common import file_sha256, read_json
from experiments.search import freeze_json
from scraping.acquisition_scope import is_jra_race_id, normalized_date
from scraping.missingness_goal import _protected
from scraping.quality import _finish_is_domain_nonfinish, _finish_time_available
from scraping.saved_horse_reuse import _present
from training.period_quality import audit_period_features
import sqlite3

BASE = ROOT / "reports" / "generated" / "data-quality" / "feature-gap"


def audit_raw(database: Path, start_year: int, until: str) -> dict:
    reports, races, counts = {}, {}, Counter()
    digest = hashlib.sha256()
    for year in range(start_year, int(until[:4]) + 1):
        reports[str(year)] = {
            scope: {"races": 0, "rows": 0, "runner_count_mismatch_races": 0,
                    "missing": dict.fromkeys(("horse_id", "sire", "dam", "damsire",
                                              "active_finish_time", "active_last3f"), 0),
                    "pedigree_incomplete_rows": 0, "examples": []}
            for scope in ("jra", "other_venues")
        }
    with closing(sqlite3.connect(database.resolve(strict=True).as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        for race_id, body in db.execute("SELECT race_id,data FROM races_ultimate ORDER BY race_id"):
            item = json.loads(body)
            day = normalized_date(item.get("date") or item.get("race_date"))
            if day is None:
                counts["race_metadata_invalid_date"] += 1
                continue
            if not f"{start_year}0101" <= day <= until:
                counts["race_metadata_outside_period"] += 1
                continue
            if race_id[:4] != day[:4]:
                counts["race_metadata_identity_date_conflict"] += 1
                continue
            scope = "jra" if is_jra_race_id(race_id, day) else "other_venues"
            races[race_id] = (day[:4], scope, item.get("num_horses"))
            reports[day[:4]][scope]["races"] += 1
            digest.update(json.dumps([race_id, body], ensure_ascii=True).encode())
        observed_counts = Counter()
        for row_id, race_id, body in db.execute(
            "SELECT id,race_id,data FROM race_results_ultimate ORDER BY id"
        ):
            if race_id not in races:
                counts["result_rows_without_in_period_valid_metadata"] += 1
                continue
            year, scope, _ = races[race_id]
            row = json.loads(body)
            report = reports[year][scope]
            report["rows"] += 1
            observed_counts[race_id] += 1
            digest.update(json.dumps([row_id, race_id, body], ensure_ascii=True).encode())
            absent = [name for name in ("horse_id", "sire", "dam", "damsire")
                      if not _present(row.get(name))]
            for name in absent:
                report["missing"][name] += 1
            if any(name in absent for name in ("sire", "dam", "damsire")):
                report["pedigree_incomplete_rows"] += 1
                if len(report["examples"]) < 3:
                    report["examples"].append({
                        "race_id": race_id, "horse_id": row.get("horse_id"), "missing": absent,
                    })
            finish = row.get("finish_position")
            if finish is None:
                finish = row.get("finish")
            if not _finish_is_domain_nonfinish(finish):
                report["missing"]["active_finish_time"] += int(not _finish_time_available(row))
                last3f = row.get("last_3f")
                if not _present(last3f):
                    last3f = row.get("last3f")
                report["missing"]["active_last3f"] += int(not _present(last3f))
        for race_id, (year, scope, declared) in races.items():
            if not isinstance(declared, int) or declared != observed_counts[race_id]:
                reports[year][scope]["runner_count_mismatch_races"] += 1
    return {
        "database": str(database.resolve()), "read_only_consistent_transaction": True,
        "selected_content_sha256": digest.hexdigest(), "years": reports,
        "outside_or_invalid": dict(counts), "calendar_universe_completeness_proven": False,
        "missing_reasons": "Names/metrics absent in storage; fetch/parse failure not inferred.",
    }


def run(root: Path, dataset: Path, database: Path, until: str) -> dict:
    root = root.resolve()
    today = datetime.now().strftime("%Y%m%d")
    if normalized_date(until) != until or not "20160101" <= until <= min(today, "20261231"):
        raise ValueError("period-audit-invalid-or-future-cutoff")
    if root.parent != BASE.resolve():
        raise ValueError("period-audit-output-outside-feature-gap")
    root.mkdir()
    protected = _protected()
    manifest_path = dataset / "dataset_manifest.json"
    manifest = read_json(manifest_path)
    path = dataset / "dataset.parquet"
    checksum = file_sha256(path)
    if checksum != manifest["dataset_sha256"]:
        raise ValueError("period-audit-baseline-hash-mismatch")
    features = manifest["feature_sets"]["M03"]
    frame = pd.read_parquet(path, columns=list(dict.fromkeys(
        ["race_id", "horse_id", "race_date", *features],
    )))
    model = audit_period_features(
        frame, features, manifest["categorical_features"], start_year=2016, end_date=until,
    )
    freeze_json(root / "feature-audit.json", model)
    print(json.dumps({"stage": "features_audited", "rows": model["rows"],
                      "missing_cells": model["missing_cells"]}), flush=True)
    raw = audit_raw(database, 2016, until)
    freeze_json(root / "raw-audit.json", raw)
    if checksum != file_sha256(path) or _protected() != protected:
        raise ValueError("period-audit-protected-state-changed")
    reasons = ["calendar_universe_not_proven", "global_structural_source_authorization_incomplete"]
    if model["missing_cells"]:
        reasons.append("model_feature_missingness_remains")
    if any(item["pedigree_incomplete_rows"] or item["runner_count_mismatch_races"]
           for year in raw["years"].values() for item in year.values()):
        reasons.append("raw_pedigree_or_runner_coverage_incomplete")
    result = {
        "verified": True, "scope": "all_existing_rows_not_all_provider_races",
        "start_date": "20160101", "end_date": until,
        "frozen_research_cohort": str(dataset.resolve()),
        "dataset_sha256": checksum, "manifest_sha256": file_sha256(manifest_path),
        "research_feature_rows": model["rows"], "research_feature_races": model["races"],
        "model_feature_missing_cells": model["missing_cells"],
        "full_period_zero_missing": model["missing_cells"] == 0,
        "full_reacquisition_gate_passed": not reasons, "gate_blocking_reasons": reasons,
        "network_requests": 0, "production_model_or_db_written": False,
        "production_model_and_policy_state": protected,
        "unobserved_future_2026_not_included": True,
    }
    freeze_json(root / "verification.json", result)
    freeze_json(root / "contract.json", {
        "schema": "period-missingness-audit-v1", "protected": protected,
        "code_hashes": {str(p.relative_to(ROOT)): file_sha256(p) for p in (
            Path(__file__), ROOT / "python-api" / "training" / "period_quality.py",
            ROOT / "python-api" / "training" / "structural_imputation.py",
        )},
    })
    freeze_json(root / "artifacts.json", {
        path.name: file_sha256(path) for path in root.iterdir() if path.is_file()
    })
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=BASE / "pedigree-reacquisition-20261026-pilot" / "dataset")
    parser.add_argument("--database", type=Path, default=ROOT / "keiba" / "data" / "keiba_ultimate.db")
    parser.add_argument("--until", default=datetime.now().strftime("%Y%m%d"))
    args = parser.parse_args()
    print(json.dumps(run(args.root, args.dataset, args.database, args.until), ensure_ascii=False))
