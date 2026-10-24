"""Predeclare a small label-blind Phase 2 pilot; never browse or write any DB.

Only identity/date projections are decoded. Existing frozen artifacts are hashed,
not interpreted as selection outcomes. Generated files are exclusive creations.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import sqlite3

import pyarrow.parquet as pq

from training_reconstruction_common import ROOT, SNAPSHOT, now, protection, read, sha, write
from verify_market_segments_outputs import verify_files

OUT = ROOT / "reports/generated/model-experiments/netkeiba-authenticated-phase2-v1"
PRIOR = OUT.parent / "netkeiba-authenticated-pilot-v1"
PRIOR_MANIFEST_SHA256 = "591e98bdf0c8c8fa70b7f0083f89618f8566834c80e2c9038338db0de231fb0c"
QUALITY = ROOT / "reports/generated/data-quality/market-h04-clean-v2"
COHORT = QUALITY / "clean_runners.parquet"
COHORT_SHA256 = "c0e295f27383f5efa1ea35a3d726db1ff4ffbf6648f4880a6904837986d4df12"
SNAPSHOT_SHA256 = "4cd3235848dd7574c4217f14fa7974d9af3a40a1ff895b66dec65f663e2a907c"
REQUEST = Path("C:/Users/yuki2/.codex/attachments/9d5dab7e-fc00-4f13-8ec8-cd9bc5287c73/pasted-text.txt")
YEARS = (2016, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025)
PRIOR_TRAINING = {2021: "202106010201", 2022: "202206010101"}
PRIOR_SPEED = "202609030411"
SQL_PROJECTIONS = {
    "races": "SELECT race_id,race_date FROM races_ultimate ORDER BY race_id",
    "runner_counts": "SELECT race_id,count(horse_number) AS runner_count FROM race_results_ultimate GROUP BY race_id ORDER BY race_id",
    "speed_counts": "SELECT race_id,count(horse_number) AS stored_runner_count FROM speed_figures GROUP BY race_id ORDER BY race_id",
}
ALLOWED_SQL_COLUMNS = {
    "races_ultimate": {"race_id", "race_date"},
    "race_results_ultimate": {"race_id", "horse_number"},
    "speed_figures": {"race_id", "horse_number"},
}


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def canonical_date(value):
    text = str(value).strip()
    if re.fullmatch(r"\d{8}", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    elif re.fullmatch(r"\d{4}/\d{2}/\d{2}", text):
        text = text.replace("/", "-")
    return date.fromisoformat(text).isoformat()


def authorizer(action, table, column, _database, _trigger):
    if action == sqlite3.SQLITE_SELECT:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ and column in ALLOWED_SQL_COLUMNS.get(table, set()):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION and column == "count":
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def read_identity_metadata():
    assert sha(SNAPSHOT) == SNAPSHOT_SHA256, "Frozen database changed"
    values = {}
    with closing(sqlite3.connect(SNAPSHOT.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        db.set_authorizer(authorizer)
        for name, sql in SQL_PROJECTIONS.items():
            values[name] = db.execute(sql).fetchall()
    assert sha(SNAPSHOT) == SNAPSHOT_SHA256
    return values


def read_cohort_identity():
    manifest = read(QUALITY / "artifact_manifest.json")
    assert manifest["files"]["clean_runners.parquet"] == COHORT_SHA256
    assert sha(COHORT) == COHORT_SHA256
    columns = ["race_id", "race_date", "horse_number", "horse_id"]
    rows = pq.read_table(COHORT, columns=columns, use_threads=False).to_pylist()
    dates = defaultdict(set)
    counts = Counter()
    keys = set()
    for row in rows:
        race = str(row["race_id"])
        key = (race, row["horse_number"])
        assert key not in keys, "Duplicate cohort runner identity"
        keys.add(key)
        dates[race].add(canonical_date(row["race_date"]))
        counts[race] += 1
    assert len(rows) == 84128 and len(counts) == 6109
    assert all(len(v) == 1 for v in dates.values())
    assert sha(COHORT) == COHORT_SHA256
    return {race: {"race_date": next(iter(dates[race])), "cohort_runner_count": count}
            for race, count in counts.items()}, {
                "path": COHORT.relative_to(ROOT).as_posix(), "sha256": COHORT_SHA256,
                "manifest_path": (QUALITY / "artifact_manifest.json").relative_to(ROOT).as_posix(),
                "manifest_sha256": sha(QUALITY / "artifact_manifest.json"),
                "projection_columns": columns, "projection_rows": len(rows),
                "projection_sha256": canonical_hash(rows), "unique_races": len(counts),
                "integrity": "Expected frozen file hash, exact 84128 unique runner keys, 6109 races, one date per race verified",
                "membership_scope": "Original quality-certified Market+H04 cohort, not later bet-type exclusions",
            }


def selected_quantiles(candidates, fractions):
    ordered = sorted(candidates, key=lambda r: (r["race_date"], r["race_id"]))
    assert len(ordered) >= len(fractions)
    indices = [((len(ordered) - 1) * numerator) // denominator for numerator, denominator in fractions]
    assert len(set(indices)) == len(indices)
    return [dict(ordered[index], selected_zero_based_rank=index,
                 selection_quantile=f"{numerator}/{denominator}", candidate_races=len(ordered))
            for index, (numerator, denominator) in zip(indices, fractions)]


def make_selection(metadata, cohort):
    runner_counts = dict(metadata["runner_counts"])
    speed_counts = dict(metadata["speed_counts"])
    by_year = defaultdict(list)
    invalid_date_count = 0
    for race_id, raw_date in metadata["races"]:
        race_id = str(race_id)
        if not re.fullmatch(r"\d{12}", race_id) or not 1 <= int(race_id[4:6]) <= 10:
            continue
        try:
            race_date = canonical_date(raw_date)
        except (ValueError, TypeError):
            invalid_date_count += 1
            continue
        year = int(race_date[:4])
        if int(race_id[:4]) != year or runner_counts.get(race_id, 0) <= 0:
            continue
        member = race_id in cohort
        if member:
            assert cohort[race_id]["race_date"] == race_date, "Cohort and frozen date conflict"
        by_year[year].append({"race_id": race_id, "race_date": race_date,
                              "identity_runner_count": runner_counts[race_id],
                              "frozen_speed_runner_count": speed_counts.get(race_id, 0),
                              "market_h04_cohort_member": member})
    training = []
    speed = []
    candidate_projections = {}
    for year in YEARS:
        candidates = [r for r in by_year[year] if r["race_id"] != PRIOR_TRAINING.get(year)]
        if year in PRIOR_TRAINING:
            candidates = [r for r in candidates if r["market_h04_cohort_member"]]
            fractions = [(1, 5), (2, 5), (3, 5), (4, 5)]
        else:
            fractions = [(1, 5), (4, 5)]
        selected = selected_quantiles(candidates, fractions)
        training.extend(selected)
        speed.extend([selected[0], selected[-1]] if year in PRIOR_TRAINING else [selected[0]])
        candidate_projections[str(year)] = {"count": len(candidates),
                                            "sha256": canonical_hash(sorted(candidates, key=lambda r: (r["race_date"], r["race_id"]))),
                                            "scope": "verified Market+H04 cohort" if year in PRIOR_TRAINING else "frozen JRA race identities"}
    current = [r for rows in by_year.values() for r in rows
               if r["race_id"] != PRIOR_SPEED and 10 <= r["frozen_speed_runner_count"] <= 18
               and r["identity_runner_count"] == r["frozen_speed_runner_count"]]
    current.sort(key=lambda r: (-date.fromisoformat(r["race_date"]).toordinal(), r["race_id"]))
    current = current[:2]
    assert len(training) == 22 and len(speed) == 11 and len(current) == 2
    return {"historical_training": training, "historical_speed": speed,
            "current_db_speed": current, "candidate_identity_projection": candidate_projections,
            "invalid_frozen_jra_date_rows_excluded": invalid_date_count}


def main():
    assert not (OUT / "phase2_pilot_plan.json").exists(), "Plan exists; never overwrite"
    assert not (OUT / "protection-before.json").exists(), "Protection baseline exists; never overwrite"
    manifest_path = PRIOR / "artifact_manifest.json"
    assert sha(manifest_path) == PRIOR_MANIFEST_SHA256, "Prior pilot manifest changed"
    prior_manifest = read(manifest_path)
    prior_counts = {"files": verify_files(PRIOR, prior_manifest["files"]),
                    "code_and_tests": verify_files(ROOT, prior_manifest["code_and_tests_sha256"])}
    before = protection()
    write(OUT / "protection-before.json", {"observed_at": now(),
          "scope": "Phase 2 before bounded browser probes; read-only protection helpers",
          "state": before, "prior_pilot_manifest_sha256": PRIOR_MANIFEST_SHA256,
          "prior_pilot_verified": prior_counts, "collector_dynamic_state_excluded_from_equality": True})
    metadata = read_identity_metadata()
    cohort, cohort_provenance = read_cohort_identity()
    selections = make_selection(metadata, cohort)
    plan = {
        "schema": "netkeiba-authenticated-phase2-plan-v1", "recorded_at": now(),
        "initial_status": "PARTIAL_PILOT", "phase_complete": False,
        "TRAINING_FEATURE_RESEARCH_READY": False, "SPEED_FEATURE_RESEARCH_READY": False,
        "MODEL_PRODUCTION_READY": False, "BACKFILL_PILOT_READY": False,
        "training_candidates_initial": {"READY": 0, "HOLD": 19, "REJECT": 1},
        "user_request_sha256": sha(REQUEST), "script_sha256": sha(Path(__file__)),
        "prior_pilot": {"path": PRIOR.relative_to(ROOT).as_posix(),
                        "manifest_sha256": PRIOR_MANIFEST_SHA256, "verified": prior_counts,
                        "training_2021": PRIOR_TRAINING[2021], "training_2022": PRIOR_TRAINING[2022],
                        "current_training": "202606040611", "current_speed": PRIOR_SPEED},
        "input_provenance": {"frozen_db": {"path": SNAPSHOT.relative_to(ROOT).as_posix(),
                             "sha256": SNAPSHOT_SHA256, "access": "mode=ro&immutable=1; query_only=ON; restrictive SQLite authorizer",
                             "sql_projections": SQL_PROJECTIONS,
                             "allowed_columns": {k: sorted(v) for k, v in ALLOWED_SQL_COLUMNS.items()},
                             "identity_projection_sha256": {k: canonical_hash(v) for k, v in metadata.items()}},
                             "cohort": cohort_provenance},
        "selection_policy": {
            "outcome_odds_labels_or_model_performance_used": False,
            "historical": "Sort valid frozen JRA identities by race_date then race_id; select floor((n-1)*q).",
            "historical_quantiles": {"other_years": ["1/5", "4/5"], "2021_2022": ["1/5", "2/5", "3/5", "4/5"]},
            "2021_2022": "Restrict to hash-verified Market+H04 cohort and exclude prior pilot race. Four new races per year yield five with prior pilot.",
            "speed_historical": "Reuse first selected training race in each year and last selected training race in 2021/2022; no value-based selection.",
            "current_db_speed": "Stored 10-18 speed runner identities, identical runner row counts; latest race_date then race_id ascending, excluding prior speed race.",
            "sample_bias": "Convenience sample of frozen DB race identities and certified research cohort, deterministic temporal spread. DB presence and course coverage may be biased.",
            "annual_availability_rate_inference_permitted": False,
            "horse_course_adaptation": "Current additional races may be selected only using public race identity/date and observed training course structure; never outcomes or performance.",
        },
        "selections": selections,
        "current_training_and_forward": {
            "target_additional_races": 2, "race_ids": ["202609040611", "202606040612"],
            "status": "FIXED_ID_CANDIDATES_FROM_TRACK_A_READ_ONLY_SCHEDULE_REQUIRING_VISIBLE_SCHEDULE_CONFIRMATION",
            "selection_basis": "Root observed queued 202609040611 and proposed 202606040612 from current schedule; browser must verify both visible race dates/start times before any forward claim.",
            "exclude_prior_training": "202606040611", "required_before_capture": ["visible race_id", "displayed race date", "displayed scheduled start time", "fetched_at before start for forward claim"],
            "course_priority": ["栗坂", "美坂", "CW/栗W", "美W"],
            "optional_courses": ["ポリトラック", "芝", "ダート"],
            "no_course_coverage_claim_from_unseen_pages": True,
        },
        "page_budget": {"hard_max_browser_navigations": 48,
            "historical_training": 22, "historical_speed": 11, "additional_current_training": 2,
            "additional_current_db_speed": 2, "forward_speed_same_two_current_training_races": 2,
            "intermediate_workout_page": 1, "master_page": 1,
            "base_research_pages": 41, "shared_optional_navigation_and_help_reserve": 7,
            "reserve_policy": "Schedule discovery, official definition pages, optional second Master/intermediate page, and any revisit all consume the same seven-page reserve. Stop at 48 overall. No retries after a stop signal.",
            "network_request_count": "Not equal to navigation count; browser subresources are not enumerated or represented as 48 HTTP requests."},
        "acquisition_controls": {"single_browser_lane": True, "minimum_navigation_gap_seconds": 3,
            "pacer_required": True, "track_a_priority": True,
            "pause_for_track_a_capture_window": True, "never_catch_up_by_accelerating": True,
            "stop_signals": ["login/authentication loss", "challenge/CAPTCHA", "HTTP 429", "HTTP 403", "service error or access restriction", "uncertain permitted page scope", "Track A unhealthy or conflicting capture window"],
            "authentication_bypass": False, "automatic_login": False,
            "raw_http_available_default": False, "save_policy": "Minimal allowlisted DOM projection or sanitized table only; no full page, credentials, personal/account details, headers, cookies or tokens.",
            "historical_source_pit": "DISPLAY_ONLY unless independent contemporary publication/version evidence exists; dates alone do not establish STRICT_PIT",
            "forward_source_pit": "Require observed timezone-qualified scheduled_start_time and fetched_at < race_start_time for this exact captured sample; preserve current-versus-historical distinction.",
        },
        "prohibited": ["bulk backfill", "existing training correction", "speed_figures overwrite", "H04 change", "Track A change", "Production change", "model fitting", "Optuna", "ROI search", "real purchase", "commit", "push"],
        "intermediate_event_schema_proposal": {"identity": ["race_id", "horse_number", "source_page_type", "source_projection_sha256", "training_sequence"],
            "fields": ["training_date", "days_before_race", "training_type", "is_final_workout_verified", "course", "track_condition", "rider", "slots", "pace", "grade", "comment_exists", "fetched_at"],
            "existing_is_last_training_equals_1_used": False,
            "absence_interpretation": "No intermediate event visible in one page type is not evidence that none exists."},
        "planned_measurement": {"training_comparison_classes": ["EXACT_CORRECT", "VALUE_CORRECT_COLUMN_WRONG", "VALUE_MISSING", "TRUNCATED", "SELECTOR_ERROR", "SOURCE_MISSING"],
            "speed_fields": ["max_index", "avg_5_index", "dist_max_index", "course_max_index", "index_last", "index_2ago", "index_3ago"],
            "speed_counts": ["compared", "exact_match", "both_missing", "mismatch", "exact_match_rate"],
            "annual_categories": ["FULL", "PARTIAL", "DISPLAY_ONLY", "NONE", "UNKNOWN"],
            "definitions_unknown_until_official_evidence": True,
            "correlation_and_rank_agreement": "DEFERRED; insufficient sample and no model fitting allowed",
            "readiness_requires_evidence": True, "backfill_yes_never_starts_collection": True},
    }
    write(OUT / "phase2_pilot_plan.json", plan)
    print(json.dumps({"plan": str(OUT / "phase2_pilot_plan.json"), "prior_verified": prior_counts,
                     "training": [[r["race_id"], r["race_date"]] for r in selections["historical_training"]],
                     "speed": [[r["race_id"], r["race_date"]] for r in selections["historical_speed"]],
                     "current_db_speed": selections["current_db_speed"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
