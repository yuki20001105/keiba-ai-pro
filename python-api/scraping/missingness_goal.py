"""Finite, isolated missingness repair with an unchanged real-data cohort."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from itertools import zip_longest
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from urllib.parse import urlsplit

import pandas as pd

from experiments.common import REPO_ROOT, active_pointer_hash, file_sha256, read_json, source_manifest
from experiments.dataset import _race_metadata, build_dataset
from experiments.goal_diagnostics import diagnose_dataset
from experiments.search import freeze_json
from scraping import pedigree_gap_repair as repair
from scraping.race_class import explicit_race_class


PARENT = REPO_ROOT / "reports" / "generated" / "data-quality" / "feature-gap"
YEARS = [2023, 2024, 2025]
IDENTITIES = ["race_id", "horse_id", "horse_number", "race_date"]
PEDIGREE_FEATURES = [
    f"safe_{entity}_{field}" for entity in ("sire", "damsire")
    for field in ("starts", "win_rate", "top3_rate")
]


def _codes():
    paths = [Path(__file__), *(Path(__file__).with_name(name) for name in (
        "horse.py", "race.py", "mobile_race.py", "fetch_pipeline.py", "request_pacing.py",
        "last3f_evidence.py",
    ))]
    return {**source_manifest(), **repair.code_hashes(),
            **{str(path.relative_to(REPO_ROOT)): file_sha256(path) for path in paths}}


def _protected():
    return {"active_model": active_pointer_hash(), **{
        name: file_sha256(REPO_ROOT / "config" / name)
        for name in ("model_acceptance_contract.v1.json", "phase3n_staking_payout_policy.v1.json")
    }}


def _readonly(path):
    return sqlite3.connect(Path(path).resolve(strict=True).as_uri() + "?mode=ro", uri=True)


def _backup(source, target):
    with closing(_readonly(source)) as before, closing(sqlite3.connect(target)) as after:
        before.backup(after)


def _summary(diagnostics):
    return {
        "rows": diagnostics["row_count"], "races": diagnostics["race_count"],
        "missing_feature_cells": sum(item["missing_count"] for item in diagnostics["features"].values()),
        "structural_feature_cells": sum(
            item["missing_reason_counts"]["structural_missing"]
            for item in diagnostics["features"].values()
        ),
        "unknown_feature_cells": sum(
            item["missing_reason_counts"]["unknown"] for item in diagnostics["features"].values()
        ),
        "features": {name: item["missing_count"] for name, item in diagnostics["features"].items()},
    }


def _event(root, iteration, action, **values):
    with (root / "progress.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps({
            "at_utc": datetime.now(timezone.utc).isoformat(),
            "iteration": iteration, "action": action, **values,
        }, ensure_ascii=False, allow_nan=False) + "\n")


def pedigree_audit(snapshot, cache, frame):
    """Classify saved evidence, not unobserved HTTP/parse failures."""
    affected = frame.loc[frame["safe_sire_starts"].isna() | frame["safe_damsire_starts"].isna()]
    weights = affected.groupby("horse_id").size()
    states = {horse: {"invalid": False, "values": {field: set() for field in repair.FIELDS},
                      "references": 0, "sources": set()} for horse in weights.index}

    def add(horse, item, source):
        if horse not in states:
            return
        values, invalid = repair._names(item)
        if not invalid and not any(values.values()):
            return
        state = states[horse]
        state["references"] += 1
        state["sources"].add(source)
        state["invalid"] |= invalid
        for field in repair.FIELDS:
            state["values"][field].update(values[field])

    with closing(_readonly(cache)) as db:
        for horse, sire, dam, damsire in db.execute(
            "SELECT horse_id,sire,dam,damsire FROM pedigree_cache"
        ):
            add(horse, dict(zip(repair.FIELDS, (sire, dam, damsire))), "pedigree_cache")
    with closing(_readonly(snapshot)) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "horse_details" in tables:
            for horse, sire, dam, damsire in db.execute("SELECT horse_id,sire,dam,damsire FROM horse_details"):
                add(horse, dict(zip(repair.FIELDS, (sire, dam, damsire))), "horse_details")
        for raw, in db.execute("SELECT data FROM race_results_ultimate ORDER BY id"):
            item = json.loads(raw)
            add(item.get("horse_id"), item, "race_results_ultimate")
    rows = []
    for horse, state in sorted(states.items()):
        values = state["values"]
        reason = (
            "ambiguous_saved_name" if state["invalid"] else
            "conflicting_saved_pedigree" if any(len(values[field]) > 1 for field in repair.FIELDS) else
            "no_saved_pedigree" if not state["references"] else
            "incomplete_saved_pedigree" if any(not values[field] for field in repair.FIELDS) else
            "consensus_available"
        )
        rows.append({"horse_id": horse, "affected_rows": int(weights[horse]), "reason": reason,
                     "references": state["references"], "sources": sorted(state["sources"])})
    return rows


def clock_targets(frame):
    """Find only preceding cohort observations, never current/future results."""
    missing = frame.loc[
        frame["safe_past3_count"].gt(0) & frame["safe_past3_avg_last3f_time"].isna()
    ]
    result = []
    for item in missing.itertuples():
        previous = frame.loc[
            frame.horse_id.eq(item.horse_id) & frame.race_date.lt(item.race_date)
        ].sort_values(["race_date", "race_id"]).tail(3)
        result.append({
            "target_race_id": item.race_id, "target_date": item.race_date, "horse_id": item.horse_id,
            "prior_cohort_races": previous[["race_id", "race_date", "horse_number"]].to_dict("records"),
            "complete_prior_window_located": len(previous) == min(3, int(item.safe_past3_count)),
            "reason": "saved_prior_clock_missing; publication_or_capture_cause_unproven",
        })
    return result


def history_audit(snapshot, frame):
    empty = frame.loc[frame.safe_prior_starts.eq(0)]
    weights = empty.groupby("horse_id").size()
    target_dates = empty.groupby("horse_id").race_date.min().to_dict()
    earliest = {}
    with closing(_readonly(snapshot)) as db:
        races = {}
        for race, raw in db.execute("SELECT race_id,data FROM races_ultimate"):
            item = json.loads(raw)
            day = repair.normalized_date(item.get("date") or item.get("race_date"))
            if day:
                _, reason = _race_metadata(race, item)
                races[race] = (day, reason or "inside_scope_but_outside_complete_eligible_history")
        for race, raw in db.execute("SELECT race_id,data FROM race_results_ultimate"):
            item = json.loads(raw)
            horse = item.get("horse_id")
            if horse not in target_dates or race not in races:
                continue
            day, reason = races[race]
            if day < target_dates[horse] and (horse not in earliest or day < earliest[horse]["race_date"]):
                earliest[horse] = {"race_id": race, "race_date": day, "scope_reason": reason}
    rows = [{"horse_id": horse, "target_date": target_dates[horse], "affected_rows": int(weights[horse]),
             "earliest_saved_prior": earliest.get(horse),
             "reason": "earlier_saved_record_excluded_from_history" if horse in earliest
             else "no_earlier_record_in_frozen_source"} for horse in sorted(target_dates)]
    return {"row_reasons": dict(sum(
        (Counter({item["reason"]: item["affected_rows"]}) for item in rows), Counter()
    )), "earlier_record_scope_reasons": dict(Counter(item["scope_reason"] for item in earliest.values())),
        "caveat": "No earlier stored record does not establish a lifetime debut or complete provider coverage.",
        "horses": rows}


def public_pedigree(html, horse_id, url):
    from bs4 import BeautifulSoup
    from scraping.horse import _find_pedigree_table, _parse_blood_table

    soup = BeautifulSoup(html, "lxml")
    canonicals = [str(tag.get("href") or tag.get("content") or "")
                  for tag in soup.select('link[rel="canonical"], meta[property="og:url"]')]
    if not canonicals or any(
        urlsplit(value).scheme != "https"
        or urlsplit(value).netloc != urlsplit(url).netloc
        or ("/" + urlsplit(value).path.lstrip("/")).rstrip("/")
        not in {f"/horse/{horse_id}", f"/horse/ped/{horse_id}"}
        for value in canonicals
    ):
        raise ValueError("public-pedigree-canonical-horse-identity-unconfirmed")
    table = _find_pedigree_table(soup)
    if table is None:
        raise ValueError("public-pedigree-table-missing")
    item = {}
    _parse_blood_table(table, item)
    values, invalid = repair._names(item)
    if invalid or any(len(values[field]) != 1 for field in repair.FIELDS):
        raise ValueError("public-pedigree-ambiguous-or-incomplete-identity")
    return {field: next(iter(values[field])) for field in repair.FIELDS}


async def _probe_pedigree(root, audit, cache):
    import aiohttp
    from scraping.fetch_pipeline import FetchAccessBlocked, fetch_context, fetch_text

    targets = sorted((item for item in audit if item["reason"] == "no_saved_pedigree"),
                     key=lambda item: (-item["affected_rows"], item["horse_id"]))[:20]
    output = root / "pedigree-sources"
    output.mkdir()
    receipts, failures = [], 0
    with fetch_context(job_id=root.name) as metrics:
        async with aiohttp.ClientSession() as session:
            for item in targets:
                horse = item["horse_id"]
                url = f"https://db.netkeiba.com/horse/ped/{horse}/"
                receipt = {"horse_id": horse, "url": url, "historical_capture_time_verified": False,
                           "observed_at_utc": datetime.now(timezone.utc).isoformat()}
                try:
                    fetched, html = await fetch_text(
                        session, url, max_retries=1, allow_redirects=False, min_interval_sec=2.0,
                        total_timeout_sec=30, max_body_bytes=2_000_000,
                        resume_key=f"{root.name}:pedigree:{horse}",
                    )
                    receipt.update(http_status=fetched.status, source=fetched.source,
                                   final_url=fetched.url, status="source_rejected")
                    (output / f"{horse}.bin").write_bytes(fetched.body)
                    (output / f"{horse}.html").write_text(html, encoding="utf-8")
                    receipt["body_sha256"] = hashlib.sha256(fetched.body).hexdigest()
                    if fetched.status == 200 and fetched.url.rstrip("/") == url.rstrip("/"):
                        try:
                            pedigree = public_pedigree(html, horse, url)
                            with closing(sqlite3.connect(cache)) as db:
                                current = db.execute(
                                    "SELECT sire,dam,damsire FROM pedigree_cache WHERE horse_id=?", (horse,)
                                ).fetchone()
                                if current and any(repair._present(value) for value in current):
                                    raise ValueError("public-pedigree-would-replace-known-cache-value")
                                db.execute(
                                    "INSERT OR REPLACE INTO pedigree_cache(horse_id,sire,dam,damsire) VALUES(?,?,?,?)",
                                    (horse, *(pedigree[field] for field in repair.FIELDS)),
                                )
                                db.commit()
                            receipt.update(status="research_cache_enriched", pedigree=pedigree)
                        except ValueError as error:
                            receipt["reason"] = str(error)
                except FetchAccessBlocked as error:
                    receipt.update(status="provider_blocked", reason=str(error))
                receipts.append(receipt)
                freeze_json(output / f"{horse}.json", receipt)
                failures = 0 if receipt["status"] == "research_cache_enriched" else failures + 1
                if receipt["status"] == "provider_blocked" or failures >= 3:
                    break
    return {"maximum_selected_horses": 20, "maximum_consecutive_no_progress": 3,
            "network_requests": metrics.get("network_requests", 0), "receipts": receipts,
            "stop_reason": "provider_blocked" if receipts and receipts[-1]["status"] == "provider_blocked"
            else "three_attempts_without_usable_evidence" if failures >= 3 else "bounded_pass_finished"}


async def _probe_clock(root, targets, snapshot):
    """One governed URL, no retry, redirect, alternate host or database repair."""
    import aiohttp
    from scraping.fetch_pipeline import FetchAccessBlocked, fetch_context, fetch_text, get_fetch_control_status
    from scraping.last3f_evidence import parse_last3f_evidence

    if not targets or not targets[0]["prior_cohort_races"]:
        return {"status": "no_located_target", "network_requests": 0}
    target = targets[0]
    prior = target["prior_cohort_races"][-1]
    url = f"https://db.netkeiba.com/race/{prior['race_id']}/"
    with closing(_readonly(snapshot)) as db:
        runners = [json.loads(raw) for raw, in db.execute(
            "SELECT data FROM race_results_ultimate WHERE race_id=? ORDER BY id", (prior["race_id"],)
        )]
    receipt = {"url": url, "horse_id": target["horse_id"], "prior_race": prior,
               "observed_at_utc": datetime.now(timezone.utc).isoformat(),
               "historical_capture_time_verified": False, "applied_to_dataset": False,
               "control_before": get_fetch_control_status()}
    with fetch_context(job_id=root.name) as metrics:
        try:
            async with aiohttp.ClientSession() as session:
                fetched, html = await fetch_text(
                    session, url, max_retries=1, allow_redirects=False,
                    min_interval_sec=2.0, total_timeout_sec=30,
                    max_body_bytes=2_000_000, resume_key=f"{root.name}:clock",
                )
            receipt.update(status="source_inspected", http_status=fetched.status,
                           source=fetched.source, final_url=fetched.url)
            (root / "clock-source.html").write_text(html, encoding="utf-8")
            (root / "clock-source.bin").write_bytes(fetched.body)
            receipt["body_sha256"] = hashlib.sha256(fetched.body).hexdigest()
            receipt["published_clock"] = "unconfirmed"
            if fetched.status == 200 and fetched.url.rstrip("/") == url.rstrip("/"):
                stamp = (fetched.cache_snapshot or {}).get("fetched_at")
                observed = (datetime.fromtimestamp(stamp, timezone.utc).isoformat() if stamp
                            else datetime.now(timezone.utc).isoformat())
                try:
                    evidence = parse_last3f_evidence(
                        html, race_id=prior["race_id"], source_url=url, observed_at_utc=observed,
                        raw_body_sha256=receipt["body_sha256"], raw_body=fetched.body,
                        expected_date=prior["race_date"], expected_runners=runners,
                        allow_cached_transport_identity=stamp is not None,
                        cache_receipt={"normalized_url": url, "status": fetched.status,
                                       "body_sha256": receipt["body_sha256"], "fetched_at": stamp},
                    )
                    freeze_json(root / "clock-source-evidence.json", evidence)
                    receipt["whole_race_identity_verified"] = True
                    observed_row = next(row for row in evidence["rows"] if row["horse_id"] == target["horse_id"])
                    receipt.update(published_clock=observed_row["last3f_state"],
                                   raw_clock=observed_row["source_raw"],
                                   source_observed_at_utc=evidence["source"]["observed_at_utc"])
                except ValueError as error:
                    receipt["source_evidence_rejection"] = str(error)
        except FetchAccessBlocked as error:
            receipt.update(status="provider_blocked", error=str(error))
        receipt["network_requests"] = metrics.get("network_requests", 0)
    if receipt["network_requests"] > 1:
        raise ValueError("missingness-probe-request-budget-exceeded")
    receipt["control_after"] = get_fetch_control_status()
    return receipt


def _compare(before, after, allowed):
    pd.testing.assert_frame_equal(before[IDENTITIES], after[IDENTITIES])
    pd.testing.assert_frame_equal(before.drop(columns=allowed), after.drop(columns=allowed))
    return len(before)


def _verify_journal(original, repaired, run):
    plan, plan_sha = repair.validate_plan(run)
    expected = {item["row_id"]: item for item in repair.jsonl_read(run / "planned.jsonl")}
    changed = 0
    with closing(_readonly(original)) as old, closing(_readonly(repaired)) as new:
        for before, after in zip_longest(
            old.execute("SELECT id,race_id,data FROM race_results_ultimate ORDER BY id"),
            new.execute("SELECT id,race_id,data FROM race_results_ultimate ORDER BY id"),
        ):
            if before is None or after is None or before[:2] != after[:2]:
                raise ValueError("missingness-source-row-identity-changed")
            if before[2] == after[2]:
                if before[0] in expected:
                    raise ValueError("missingness-planned-repair-not-applied")
                continue
            target = expected.get(before[0])
            if target is None or repair.digest(before[2]) != target["before_sha256"]:
                raise ValueError("missingness-unplanned-source-update")
            item = json.loads(before[2])
            for field, value in target["fill"].items():
                if repair._present(item.get(field)):
                    raise ValueError("missingness-replaced-known-value")
                item[field] = value
            if item != json.loads(after[2]):
                raise ValueError("missingness-nonpedigree-source-update")
            changed += 1
        journal = new.execute(
            "SELECT COUNT(*) FROM data_quality_repair_journal_v1 WHERE batch_id=?", (run.name,)
        ).fetchone()[0] if expected else 0
    if changed != len(expected) or journal != changed:
        raise ValueError("missingness-journal-count-mismatch")
    return {"changed_rows": changed, "plan_sha256": plan_sha,
            "historical_capture_time_verified": False}


def verify(root):
    root = Path(root).resolve(strict=True)
    task = read_json(root / "task-ledger.json")
    if _codes() != task["code_hashes"] or _protected() != task["protected"]:
        raise ValueError("missingness-code-or-protected-state-changed")
    for path, digest in task["input_hashes"].items():
        if file_sha256(Path(path)) != digest:
            raise ValueError("missingness-frozen-input-changed")
    for name, digest in read_json(root / "artifacts.json").items():
        if file_sha256(root / name) != digest:
            raise ValueError("missingness-artifact-changed")
    total_requests = 0
    if (root / "clock-source-receipt.json").exists():
        clock = read_json(root / "clock-source-receipt.json")
        if clock["network_requests"] > 1:
            raise ValueError("missingness-clock-network-budget-exceeded")
        total_requests += clock["network_requests"]
    if (root / "pedigree-probe.json").exists():
        probe = read_json(root / "pedigree-probe.json")
        if len(probe["receipts"]) > 20 or probe["network_requests"] > 20:
            raise ValueError("missingness-pedigree-network-budget-exceeded")
        total_requests += probe["network_requests"]
        with closing(_readonly(root / "pedigree-cache-before.db")) as before_cache, closing(
            _readonly(root / "pedigree-cache.db")
        ) as after_cache:
            expected_cache = {row[0]: row[1:] for row in before_cache.execute(
                "SELECT horse_id,sire,dam,damsire FROM pedigree_cache"
            )}
            for receipt in probe["receipts"]:
                if receipt["status"] != "research_cache_enriched":
                    continue
                horse = receipt["horse_id"]
                if any(repair._present(value) for value in expected_cache.get(horse, ())):
                    raise ValueError("missingness-public-source-overwrote-known-value")
                source = root / "pedigree-sources" / f"{horse}.html"
                pedigree = public_pedigree(source.read_text(encoding="utf-8"), horse, receipt["url"])
                if pedigree != receipt["pedigree"]:
                    raise ValueError("missingness-public-pedigree-not-reproducible")
                expected_cache[horse] = tuple(pedigree[field] for field in repair.FIELDS)
            actual = {row[0]: row[1:] for row in after_cache.execute(
                "SELECT horse_id,sire,dam,damsire FROM pedigree_cache"
            )}
            if actual != expected_cache:
                raise ValueError("missingness-unproven-public-cache-update")
    if total_requests > task["maximum_network_requests"]:
        raise ValueError("missingness-total-network-budget-exceeded")
    baseline = Path(task["baseline_dataset"])
    frames = [pd.read_parquet(path / "dataset.parquet") for path in (
        baseline, root / "iteration-01", root / "iteration-02"
    )]
    _compare(frames[0], frames[1], ["safe_race_class"])
    _compare(frames[1], frames[2], PEDIGREE_FEATURES)
    known = frames[0].safe_race_class.notna()
    pd.testing.assert_series_equal(frames[0].loc[known, "safe_race_class"],
                                   frames[1].loc[known, "safe_race_class"])
    with closing(_readonly(task["snapshot"])) as db:
        names = {race: json.loads(raw).get("race_name")
                 for race, raw in db.execute("SELECT race_id,data FROM races_ultimate")}
    changed = frames[0].safe_race_class.isna() & frames[1].safe_race_class.notna()
    for race, values in frames[1].loc[changed].groupby("race_id"):
        if values.safe_race_class.nunique() != 1 or explicit_race_class(names[race]) != values.safe_race_class.iloc[0]:
            raise ValueError("missingness-class-without-explicit-source")
    summaries = []
    for index, path in enumerate((baseline, root / "iteration-01", root / "iteration-02")):
        manifest = read_json(path / "dataset_manifest.json")
        if file_sha256(path / "dataset.parquet") != manifest["dataset_sha256"]:
            raise ValueError("missingness-dataset-hash-mismatch")
        summary = _summary(diagnose_dataset(path, task["development_years"]))
        if index and summary["missing_feature_cells"] > summaries[-1]["missing_feature_cells"]:
            raise ValueError("missingness-repair-introduced-new-gaps")
        summaries.append(summary)
    journal = _verify_journal(Path(task["snapshot"]), root / "research.db", root / "pedigree-plan")
    result = {"schema": "missingness-goal-verification-v1", "verified": True,
              "zero_missingness_goal_met": summaries[-1]["missing_feature_cells"] == 0,
              "stop_reason": "bounded_pass_completed; remaining_history_and_source_identity_gaps",
              "same_cohort": True, "source_outcomes_and_nonpedigree_values_preserved": True,
              "protected_state_unchanged": True, "summaries": summaries, "pedigree_journal": journal,
              "no_production_result_db_or_model_writes": True,
              "network_requests": total_requests,
              "prestart_availability_established": False, "task_sha256": file_sha256(root / "task-ledger.json")}
    return result


def run(root, baseline_campaign, *, probe_clock=False, probe_pedigree=False):
    root = Path(root).resolve()
    if (root.parent != PARENT.resolve() or root.is_symlink()
            or any(parent.is_symlink() for parent in root.parents)):
        raise ValueError("missingness-run-must-be-new-direct-feature-gap-child")
    root.mkdir(parents=True, exist_ok=False)
    baseline_campaign = Path(baseline_campaign).resolve(strict=True)
    old_task = read_json(baseline_campaign / "task-ledger.json")
    snapshot = Path(old_task["snapshot"]["path"]).resolve(strict=True)
    baseline = baseline_campaign / "dataset"
    cache = root / "pedigree-cache.db"
    before_cache = root / "pedigree-cache-before.db"
    _backup(REPO_ROOT / "keiba" / "data" / "pedigree_cache.db", before_cache)
    shutil.copyfile(before_cache, cache)
    task = {
        "schema": "missingness-goal-task-v1",
        "goal": "Zero real feature gaps without inventing observations, shrinking cohorts or backdating availability.",
        "baseline_campaign": str(baseline_campaign), "baseline_dataset": str(baseline),
        "baseline_is_previously_exposed_development_data": True,
        "snapshot": str(snapshot), "development_years": YEARS,
        "maximum_iterations": 2, "maximum_network_requests": int(probe_clock) + 20 * int(probe_pedigree),
        "shared_http_cache_writes_permitted": probe_clock or probe_pedigree,
        "permitted_repairs": ["explicit_saved_race_class", "existing_strict_static_pedigree_consensus",
                              "bounded_current_public_pedigree_in_research_copy_only"],
        "code_hashes": _codes(), "protected": _protected(),
        "input_hashes": {str(path): file_sha256(path) for path in (
            snapshot, baseline / "dataset.parquet", baseline / "dataset_manifest.json", before_cache,
        )},
        "unresolved_policy": "Keep NaN and evidence gaps; stop when the permitted evidence is exhausted.",
    }
    freeze_json(root / "task-ledger.json", task)
    try:
        _event(root, 0, "frozen")
        before = diagnose_dataset(baseline, YEARS)
        freeze_json(root / "baseline-diagnostics.json", before)
        build_dataset(snapshot, root / "iteration-01")
        _event(root, 1, "explicit_saved_class_normalization")
        frame = pd.read_parquet(baseline / "dataset.parquet", filters=[("race_date", "<=", "20251231")])
        audit = pedigree_audit(snapshot, cache, frame)
        freeze_json(root / "pedigree-horse-findings.json", audit)
        freeze_json(root / "pedigree-findings.json", {
            "horse_reasons": dict(Counter(item["reason"] for item in audit)),
            "row_reasons": dict(sum(
                (Counter({item["reason"]: item["affected_rows"]}) for item in audit), Counter()
            )),
            "source_failure_cause": "Not inferred from missingness; no_saved is not proof of fetch failure.",
            "ambiguous_name_policy": "No country-tag stripping or guessed bilingual-name equivalence.",
        })
        targets = clock_targets(frame)
        freeze_json(root / "clock-targets.json", targets)
        freeze_json(root / "history-findings.json", history_audit(snapshot, frame))
        if probe_pedigree:
            freeze_json(root / "pedigree-probe.json", asyncio.run(_probe_pedigree(root, audit, cache)))
        if probe_clock:
            freeze_json(root / "clock-source-receipt.json", asyncio.run(_probe_clock(root, targets, snapshot)))
        shutil.copyfile(snapshot, root / "research.db")
        plan_dir = root / "pedigree-plan"
        plan_dir.mkdir()
        plan = repair.plan_repair(root / "research.db", cache, plan_dir)
        if plan["counts"].get("planned_rows", 0):
            execution = repair.apply_repair(plan_dir)
            if execution["remaining_planned_rows"] != 0:
                raise ValueError("missingness-pedigree-plan-incomplete")
            freeze_json(root / "pedigree-execution.json", execution)
        # The dataset builder requires a standalone SQLite file, not a WAL.
        with closing(sqlite3.connect(root / "research.db")) as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            db.execute("PRAGMA journal_mode=DELETE")
        build_dataset(root / "research.db", root / "iteration-02")
        _event(root, 2, "strict_saved_pedigree_reconstruction",
               planned_rows=plan["counts"].get("planned_rows", 0))
        artifacts = {str(path.relative_to(root)): file_sha256(path)
                     for path in sorted(root.rglob("*")) if path.is_file()
                     and path.name not in {"artifacts.json", "verification.json", "failure.json", "progress.jsonl"}}
        freeze_json(root / "artifacts.json", artifacts)
        result = verify(root)
        freeze_json(root / "verification.json", result)
        _event(root, 2, "verified_stopped", goal_met=result["zero_missingness_goal_met"])
        return result
    except Exception as error:
        freeze_json(root / "failure.json", {"error": type(error).__name__ + ": " + str(error)})
        _event(root, -1, "failed", error=type(error).__name__ + ": " + str(error))
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["repair-missingness", "verify-missingness"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--probe-clock", action="store_true")
    parser.add_argument("--probe-pedigree", action="store_true")
    args = parser.parse_args()
    if args.action == "repair-missingness":
        if args.baseline is None:
            parser.error("--baseline is required")
        result = run(args.root, args.baseline, probe_clock=args.probe_clock, probe_pedigree=args.probe_pedigree)
    else:
        result = verify(args.root)
    print(json.dumps({key: value for key, value in result.items() if key != "summaries"},
                     ensure_ascii=False, allow_nan=False))
    return 0 if result["zero_missingness_goal_met"] else 2
