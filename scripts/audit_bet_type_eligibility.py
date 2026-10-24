"""Read-only premetric eligibility audit; new exclusive output directory only."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[key] = "2"
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "scripts"), str(ROOT / "keiba")]
import pandas as pd
import pyarrow.parquet as pq
from bet_types.eligibility import audit_eligibility, encoded
from verify_market_segments_outputs import sha256, read, require, verify_files, _protected_state, _quality_check

QUALITY = ROOT / "reports/generated/data-quality/market-h04-clean-v2"
SEGMENTS = ROOT / "reports/generated/model-experiments/market-segments-v1"
SNAPSHOT_SHA = "505f00e514df2139df3746c6213a7e5b6b1ff24db04d505a4238f6bd657c6419"
SNAPSHOT = ROOT / f"reports/generated/model-experiments/20260919-jra-safe-v1/snapshots/{SNAPSHOT_SHA}.db"
COLUMNS = ["race_id", "race_date", "horse_number", "finish", "field_size", "expected_runner_count", "phase"]


def read_scope(snapshot, race_ids):
    path = Path(snapshot).resolve(strict=True)
    wal = Path(str(path) + "-wal")
    require(not wal.exists() or not wal.stat().st_size, "snapshot has pending WAL")
    ids = sorted(set(race_ids))
    require(ids and all(len(rid) == 12 and rid.isdigit() and rid[:4] in {"2021", "2022"} for rid in ids), "2021/2022 IDs required")
    source = {"races": defaultdict(list), "payouts": defaultdict(list)}
    digest = hashlib.sha256()
    with sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        for offset in range(0, len(ids), 300):
            chunk = ids[offset:offset + 300]
            marks = ",".join("?" for _ in chunk)
            for kind, query in (
                ("races", f"SELECT race_id,data,created_at FROM races_ultimate WHERE race_id IN ({marks}) ORDER BY race_id"),
                ("payouts", f"SELECT race_id,id,bet_type,combinations,payout,popularity,created_at FROM return_tables_ultimate WHERE race_id IN ({marks}) ORDER BY race_id,id")):
                for row in db.execute(query, chunk):
                    value = dict(row)
                    source[kind][str(row["race_id"])].append(value)
                    digest.update((encoded([kind, value]) + "\n").encode("utf-8"))
    return source, {"scoped_source_sha256": digest.hexdigest(), "source_races": len(ids),
                    "read_row_counts": {k: sum(map(len, v.values())) for k, v in source.items()},
                    "snapshot_path": str(path), "immutable_read_only": True, "post_2022_rows_read": False}


def protection():
    state = _protected_state(ROOT)
    segment_manifest = read(SEGMENTS / "artifact_manifest.json")
    state["previous_segments_artifacts_checked"] = verify_files(SEGMENTS, segment_manifest["files"])
    state["previous_segments_manifest_sha256"] = sha256(SEGMENTS / "artifact_manifest.json")
    segment_plan = read(SEGMENTS / "segment_plan.json")
    state["previous_segments_sources_checked"] = verify_files(ROOT, segment_plan["code_sha256"])
    _quality_check(segment_plan)
    return state


def execute(output, supersedes=None):
    output = Path(output).resolve()
    require(not output.exists(), "output must be new; never overwrite an audit")
    before = protection()
    cohort_path = QUALITY / "clean_runners.parquet"
    summary = read(QUALITY / "quality_summary.json")
    require(sha256(cohort_path) == summary["clean_runners_sha256"], "released cohort changed")
    require(sha256(SNAPSHOT) == SNAPSHOT_SHA, "immutable source snapshot changed")
    cohort = pd.read_parquet(cohort_path, columns=COLUMNS,
        filters=[("race_date", ">=", "20210101"), ("race_date", "<=", "20221231")], use_threads=False)
    require(len(cohort) == pq.read_metadata(cohort_path).num_rows, "unscoped input rows")
    source, receipt = read_scope(SNAPSHOT, cohort.race_id.unique())
    race, payouts, coverage = audit_eligibility(cohort, source["races"], source["payouts"])
    after = protection()
    require(before["previous_segments_manifest_sha256"] == after["previous_segments_manifest_sha256"], "prior manifest changed")
    require(sha256(cohort_path) == summary["clean_runners_sha256"], "cohort changed during audit")
    require(sha256(SNAPSHOT) == SNAPSHOT_SHA, "immutable snapshot changed during audit")
    correction = None
    if supersedes is not None:
        previous = Path(supersedes).resolve(strict=True)
        old = read(previous / "summary.json")
        require(old["performance_metrics_computed"] is False and old["cohort_sha256"] == summary["clean_runners_sha256"]
                and read(previous / "source_receipt.json")["scoped_source_sha256"] == receipt["scoped_source_sha256"],
                "parser correction must preserve exact premetric source cohort")
        correction = {"superseded_audit": str(previous), "superseded_manifest_sha256": sha256(previous / "artifact_manifest.json"),
            "reason": "Accept only literal whitespace around separators and ordered-type hyphen separators; keep horse order; no source values changed",
            "predictive_common_cohort_unchanged": True, "performance_metrics_seen": False,
            "source_scope_sha256_unchanged": receipt["scoped_source_sha256"]}
        old_race = pd.read_csv(previous / "race_eligibility.csv", dtype={"race_id": str})
        require(old_race[["race_id", "common_phase_a_eligible"]].equals(race[["race_id", "common_phase_a_eligible"]]),
                "parser correction changed predictive selection")
    policy = {"schema": "h04-bet-types-premetric-eligibility-v1", "no_prediction_metrics_computed": True,
        "selection": "same common Phase-A races: released core-consistent complete races, no tied finish anywhere, >=8 actual starters",
        "dead_heats": "legal competition ranks remain valid source data; conservative research exclusion, not corruption or source repair",
        "place": "sales-opening scheduled count is not reconstructed; >=8 actual starters used only as conservative lower bound under no post-opening additions; 5-7 excluded from common cohort",
        "official_rule_reference": "https://www.jra.go.jp/kouza/baken/",
        "payouts": "post-race provider return tables verify labels only; never pre-race odds, EV input, or predictive-cohort selector",
        "payout_missingness": "record separately; no missing payout imputation and no silent cohort filtering",
        "later_phases": "exacta/trio/trifecta coverage only, no performance evaluation",
        "scope": "2021/2022 already-researched cohort; not independent unseen validation"}
    result = {"schema": policy["schema"], "created_at": datetime.now(timezone.utc).isoformat(),
        "coverage": coverage.to_dict("records"), "policy": policy,
        "phase1_quality_summary_sha256": sha256(QUALITY / "quality_summary.json"),
        "cohort_sha256": summary["clean_runners_sha256"], "snapshot_sha256": SNAPSHOT_SHA,
        "protected_before": before, "protected_after": after,
        "performance_metrics_computed": False, "post_2022_outcomes_read": False, "source_mutated": False}
    output.mkdir(parents=True, exist_ok=False)
    for name, frame in (("race_eligibility.csv", race), ("payout_label_audit.csv", payouts), ("coverage.csv", coverage),
                        ("excluded_races.csv", race.loc[~race.common_phase_a_eligible])):
        frame.to_csv(output / name, index=False, lineterminator="\n")
    for name, value in (("summary.json", result), ("source_receipt.json", receipt), ("eligibility_policy.json", policy)):
        with (output / name).open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
    if correction is not None:
        with (output / "parser_correction.json").open("x", encoding="utf-8") as stream:
            json.dump(correction, stream, ensure_ascii=False, indent=2)
    manifest = {"schema": "h04-bet-types-eligibility-artifacts-v1",
        "files": {p.name: sha256(p) for p in sorted(output.iterdir())},
        "code": {name: sha256(ROOT / name) for name in ("scripts/audit_bet_type_eligibility.py", "python-api/bet_types/eligibility.py")}}
    with (output / "artifact_manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
    return {"output": str(output), "manifest_sha256": sha256(output / "artifact_manifest.json"), "coverage": result["coverage"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--supersedes", type=Path)
    args = parser.parse_args()
    if os.name == "nt":
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    print(json.dumps(execute(args.output, args.supersedes), ensure_ascii=False, indent=2))
