"""Explicit, resumable static-pedigree reconstruction from saved evidence only.

This never fetches a profile, replaces a normal value, backdates observation,
or treats a reconstructed immutable pedigree as a historical capture receipt.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time

from scraping.acquisition_scope import is_jra_race_id, normalized_date
from scraping.safe_repair import _journal, digest, encode, initialize_journal
from scraping.saved_horse_reuse import _name, _present
from services.prediction_source_revision import _validated_state


SCHEMA = "saved-pedigree-gap-plan-v1"
PERIOD = ("20160101", "20261016")
FIELDS = ("sire", "dam", "damsire")
ALIASES = {"sire": ("sire", "sire_name", "father"),
           "dam": ("dam", "dam_name", "mother"),
           "damsire": ("damsire", "broodmare_sire", "dam_sire_name", "dam_sire")}
RUN_PARENT = Path("reports/generated/data-quality/feature-gap")
MAX_REFERENCE_SAMPLES = 3


def now():
    return datetime.now(timezone.utc).isoformat()


def lower_priority():
    try:
        if os.name == "nt":
            import ctypes
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
        else:
            os.nice(10)
    except (OSError, AttributeError):
        pass


def file_sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def json_write_new(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def jsonl_read(path):
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def readonly(path):
    connection = sqlite3.connect(Path(path).resolve(strict=True).as_uri() + "?mode=ro",
                                 uri=True, timeout=3, isolation_level=None)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA cache_size=-4096")
    return connection


def revision(connection):
    state = _validated_state(connection)
    if state is None:
        raise ValueError("pedigree-source-revision-unavailable")
    return list(state)


def file_identity(path):
    stat = Path(path).stat()
    return [stat.st_dev, stat.st_ino]


def _details_projection(db):
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    rows = (db.execute("SELECT horse_id,sire,dam,damsire FROM horse_details ORDER BY horse_id").fetchall()
            if "horse_details" in tables else [])
    return rows, digest(encode(rows))


def checked_run_directory(root, run_dir, *, create=False):
    root = Path(root).resolve(strict=True)
    parent = (root / RUN_PARENT).resolve()
    run = Path(run_dir)
    if not run.is_absolute():
        run = root / run
    if run.is_symlink() or any(part.is_symlink() for part in run.parents):
        raise ValueError("pedigree-linked-run-directory")
    run = run.resolve()
    if run.parent != parent or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", run.name):
        raise ValueError("pedigree-run-directory-out-of-scope")
    if create:
        parent.mkdir(parents=True, exist_ok=True)
        run.mkdir(exist_ok=False)
    elif not run.is_dir():
        raise FileNotFoundError(run)
    return run


def _names(item):
    values, invalid = {}, False
    for field, aliases in ALIASES.items():
        found = set()
        for alias in aliases:
            value = item.get(alias)
            if _present(value):
                name = _name(value)
                if name is None or re.search(r"[A-Za-z\u3040-\u30ff\u3400-\u9fff]", name) is None:
                    invalid = True
                else:
                    found.add(name)
        values[field] = found
    return values, invalid


def _cache_projection(cache_path):
    with closing(readonly(cache_path)) as db:
        before = db.execute("PRAGMA data_version").fetchone()[0]
        db.execute("BEGIN")
        rows = db.execute("SELECT horse_id,sire,dam,damsire FROM pedigree_cache ORDER BY horse_id").fetchall()
        db.commit()
        if db.execute("PRAGMA data_version").fetchone()[0] != before:
            raise ValueError("pedigree-cache-changed-during-read")
    return rows, digest(encode(rows))


def code_hashes():
    # These are small Python files, not database or model-artifact scans.
    import scraping.safe_repair as safe
    import scraping.saved_horse_reuse as reuse
    import services.prediction_source_revision as rev
    paths = (Path(__file__), Path(safe.__file__), Path(reuse.__file__), Path(rev.__file__))
    return {str(path.resolve()): file_sha(path) for path in paths}


def _check_code(expected):
    if not isinstance(expected, dict) or code_hashes() != expected:
        raise ValueError("pedigree-code-binding-changed")


def plan_repair(db_path, cache_path, run_dir, *, protected=None, cli_sha256=None,
                yield_every=1024, pause_seconds=.003):
    """Write a new sealed plan. Only the new output directory is written."""
    db_path, cache_path, run_dir = Path(db_path).resolve(strict=True), Path(cache_path).resolve(strict=True), Path(run_dir)
    if any((run_dir / name).exists() for name in ("plan.json", "planned.jsonl", "consensus_evidence.jsonl", "receipt.json")):
        raise FileExistsError("pedigree-plan-already-exists")
    if not run_dir.is_dir():
        raise FileNotFoundError(run_dir)
    lower_priority()
    database_identity = file_identity(db_path)
    observations = defaultdict(lambda: {"values": {field: set() for field in FIELDS}, "invalid": False,
        "reference_samples": [], "reference_count": 0, "reference_digest": hashlib.sha256()})
    counts = Counter()
    cache, cache_sha = _cache_projection(cache_path)

    def add(horse_id, item, reference):
        if not isinstance(horse_id, str) or not re.fullmatch(r"[A-Za-z0-9]{1,32}", horse_id):
            return
        values, invalid = _names(item)
        if not invalid and not any(values.values()):
            return
        evidence = observations[horse_id]
        evidence["invalid"] |= invalid
        for field in FIELDS:
            evidence["values"][field].update(values[field])
        reference["values"] = {field: sorted(values[field]) for field in FIELDS}
        reference["ambiguous_name"] = invalid
        # Every reference contributes, but only a small, explicit sample is
        # retained in RAM. A horse with hundreds of starts cannot grow this
        # list without bound. Source revision/cache bindings make replay of
        # this deterministic ordered digest independently possible.
        evidence["reference_count"] += 1
        evidence["reference_digest"].update((encode(reference) + "\n").encode("utf-8"))
        if len(evidence["reference_samples"]) < MAX_REFERENCE_SAMPLES:
            evidence["reference_samples"].append(reference)

    for horse_id, sire, dam, damsire in cache:
        item = dict(zip(FIELDS, (sire, dam, damsire)))
        add(str(horse_id), item, {"source": "pedigree_cache", "horse_id": str(horse_id), "value_sha256": digest(encode(item))})
    pending = []
    with closing(readonly(db_path)) as db:
        db.execute("BEGIN")
        source_before = revision(db)
        races = {}
        for race_id, raw in db.execute("SELECT race_id,data FROM races_ultimate ORDER BY race_id"):
            if not is_jra_race_id(race_id):
                continue
            info = json.loads(raw)
            day = normalized_date(info.get("date") or info.get("race_date"))
            if day and PERIOD[0] <= day <= PERIOD[1]:
                races[race_id] = day
        detail_rows, detail_sha = _details_projection(db)
        for horse_id, sire, dam, damsire in detail_rows:
            item = dict(zip(FIELDS, (sire, dam, damsire)))
            add(str(horse_id), item, {"source": "horse_details", "horse_id": str(horse_id), "value_sha256": digest(encode(item))})
        for row_id, race_id, raw in db.execute("SELECT id,race_id,data FROM race_results_ultimate ORDER BY id"):
            counts["source_runner_rows"] += 1
            item = json.loads(raw)
            horse_id = item.get("horse_id")
            add(horse_id, item, {"source": "race_results_ultimate", "row_id": row_id, "race_id": race_id, "raw_sha256": digest(raw)})
            if race_id in races:
                counts["in_scope_runner_rows"] += 1
                missing = [field for field in FIELDS if not _present(item.get(field))]
                if missing:
                    counts["in_scope_rows_with_missing_canonical_slot"] += 1
                    pending.append({"row_id": row_id, "race_id": race_id, "race_date": races[race_id],
                        "horse_id": horse_id, "before_sha256": digest(raw), "missing_fields": missing})
            if counts["source_runner_rows"] % yield_every == 0:
                time.sleep(pause_seconds)
        source_after = revision(db)
        if source_before != source_after:
            raise ValueError("pedigree-noncoherent-source-read")
        db.commit()
        latest = revision(db)
        if latest != source_before or file_identity(db_path) != database_identity:
            raise ValueError("pedigree-source-changed-during-plan")
        if _details_projection(db)[1] != detail_sha:
            raise ValueError("pedigree-horse-details-changed-during-plan")
    if _cache_projection(cache_path)[1] != cache_sha:
        raise ValueError("pedigree-cache-changed-during-plan")
    consensus = {}
    for horse_id, evidence in observations.items():
        values = evidence["values"]
        if evidence["invalid"]:
            reason = "ambiguous_saved_name"
        elif any(len(values[field]) > 1 for field in FIELDS):
            reason = "conflicting_saved_pedigree"
        elif any(not values[field] for field in FIELDS):
            reason = "incomplete_saved_pedigree"
        else:
            consensus[horse_id] = {"horse_id": horse_id,
                "pedigree": {field: next(iter(values[field])) for field in FIELDS},
                "source_references": evidence["reference_samples"],
                "source_references_are_sample_only": True,
                "total_reference_count": evidence["reference_count"],
                "all_reference_digest": evidence["reference_digest"].hexdigest(),
                "reference_digest_encoding": "sha256(concat(UTF8(safe_repair.encode(reference) + LF)))",
                "reference_order": "pedigree_cache horse_id; horse_details horse_id; race_results_ultimate id",
                "max_reference_samples": MAX_REFERENCE_SAMPLES,
                "reconstruction_kind": "static_pedigree_from_saved_evidence",
                "historical_capture_time_verified": False}
            reason = "consensus_available"
        evidence["reason"] = reason
        counts["horse_" + reason] += 1
    planned, used = [], set()
    for target in pending:
        horse_id = target["horse_id"]
        if horse_id not in consensus:
            reason = observations.get(horse_id, {}).get("reason", "no_saved_pedigree")
            counts["held_rows_" + reason] += 1
            continue
        evidence = consensus[horse_id]
        target["fill"] = {field: evidence["pedigree"][field] for field in target.pop("missing_fields")}
        target["consensus_sha256"] = digest(encode(evidence))
        counts["planned_rows"] += 1
        for field in target["fill"]:
            counts["planned_slots_" + field] += 1
        planned.append(target)
        used.add(horse_id)
    with (run_dir / "planned.jsonl").open("x", encoding="utf-8", newline="\n") as stream:
        for item in planned:
            stream.write(encode(item) + "\n")
    with (run_dir / "consensus_evidence.jsonl").open("x", encoding="utf-8", newline="\n") as stream:
        for horse_id in sorted(used):
            stream.write(encode(consensus[horse_id]) + "\n")
    value = {"schema": SCHEMA, "batch_id": run_dir.name, "created_at_utc": now(),
        "database": str(db_path), "cache_database": str(cache_path), "period": list(PERIOD),
        "race_scope": "jra", "source_revision": source_before, "database_file_identity": database_identity,
        "cache_projection_sha256": cache_sha, "horse_details_projection_sha256": detail_sha,
        "code_hashes": code_hashes(), "cli_sha256": cli_sha256,
        "protected_before": protected, "counts": dict(counts),
        "reference_storage": {"all_sources_checked": True, "max_samples_per_horse": MAX_REFERENCE_SAMPLES,
            "all_references_bound_by_ordered_sha256": True, "full_reference_lists_retained_in_memory": False},
        "planned_sha256": file_sha(run_dir / "planned.jsonl"),
        "consensus_evidence_sha256": file_sha(run_dir / "consensus_evidence.jsonl"),
        "network_requests": 0, "source_database_writes": 0,
        "historical_prestart_capture_proven": False, "physical_database_copy_or_hash": False}
    json_write_new(run_dir / "plan.json", value)
    json_write_new(run_dir / "receipt.json", {"schema": SCHEMA, "plan_sha256": file_sha(run_dir / "plan.json"), "batch_id": run_dir.name})
    return value


def validate_plan(run_dir):
    run_dir = Path(run_dir)
    plan = json.loads((run_dir / "plan.json").read_text(encoding="utf-8"))
    receipt = json.loads((run_dir / "receipt.json").read_text(encoding="utf-8"))
    if (plan.get("schema") != SCHEMA or plan.get("batch_id") != run_dir.name or receipt.get("batch_id") != run_dir.name
            or receipt.get("plan_sha256") != file_sha(run_dir / "plan.json") or plan.get("period") != list(PERIOD)
            or plan.get("race_scope") != "jra"):
        raise ValueError("pedigree-plan-binding-invalid")
    for key, name in (("planned_sha256", "planned.jsonl"), ("consensus_evidence_sha256", "consensus_evidence.jsonl")):
        if plan.get(key) != file_sha(run_dir / name):
            raise ValueError("pedigree-evidence-ledger-changed")
    _check_code(plan.get("code_hashes"))
    return plan, receipt["plan_sha256"]


def _resume_state(db, plan, plan_sha):
    expected, done = plan["source_revision"], {}
    for table, key, before, after, before_sha, after_sha, evidence in db.execute("SELECT table_name,row_key,before_data,after_data,before_sha256,after_sha256,evidence_json FROM data_quality_repair_journal_v1 WHERE batch_id=? ORDER BY id", (plan["batch_id"],)):
        item = json.loads(evidence)
        after_revision = item.get("source_revision_after")
        if (table != "race_results_ultimate" or before is None or before_sha != digest(before) or after_sha != digest(after)
                or item.get("plan_sha256") != plan_sha or item.get("source_revision_before") != expected
                or not isinstance(after_revision, list) or len(after_revision) != 3
                or after_revision[0] != expected[0] or after_revision[1] != expected[1] + 1):
            raise ValueError("pedigree-resume-journal-binding-invalid")
        expected = after_revision
        if int(key) in done:
            raise ValueError("pedigree-duplicate-journal-target")
        done[int(key)] = {"before_sha256": digest(before), "after_sha256": digest(after)}
    if revision(db) != expected:
        raise ValueError("pedigree-source-changed-replan-required")
    return expected, done


def apply_repair(run_dir, *, chunk_size=100, pause_seconds=.01, limit=None, protected_check=None):
    """Apply only a previously sealed plan; a failed chunk rolls back in full."""
    if not 1 <= chunk_size <= 100 or pause_seconds < 0 or (limit is not None and limit < 1):
        raise ValueError("pedigree-invalid-batch-budget")
    lower_priority()
    plan, plan_sha = validate_plan(run_dir)
    db_path, cache_path, run_dir = Path(plan["database"]), Path(plan["cache_database"]), Path(run_dir)
    if file_identity(db_path) != plan["database_file_identity"]:
        raise ValueError("pedigree-database-file-replaced")
    if protected_check:
        protected_check(plan["protected_before"])
    if _cache_projection(cache_path)[1] != plan["cache_projection_sha256"]:
        raise ValueError("pedigree-cache-changed-replan-required")
    consensus = {item["horse_id"]: item for item in jsonl_read(run_dir / "consensus_evidence.jsonl")}
    initialize_journal(db_path)
    counts, started = Counter(), now()
    with closing(sqlite3.connect(str(db_path), timeout=3, isolation_level=None)) as db:
        if _details_projection(db)[1] != plan["horse_details_projection_sha256"]:
            raise ValueError("pedigree-horse-details-changed-replan-required")
        expected, done = _resume_state(db, plan, plan_sha)
        execution_source_before = list(expected)
        pending = []
        seen = set()
        for target in jsonl_read(run_dir / "planned.jsonl"):
            row_id = target.get("row_id")
            if type(row_id) is not int or row_id in seen:
                raise ValueError("pedigree-plan-duplicate-or-invalid-row-id")
            seen.add(row_id)
            if row_id in done:
                current = db.execute("SELECT data FROM race_results_ultimate WHERE id=?", (row_id,)).fetchone()
                if (done[row_id]["before_sha256"] != target["before_sha256"] or current is None
                        or digest(current[0]) != done[row_id]["after_sha256"]):
                    raise ValueError("pedigree-resume-row-changed")
                counts["already_applied_rows"] += 1
            else:
                pending.append(target)
        if set(done) - seen:
            raise ValueError("pedigree-journal-has-unplanned-rows")
        if limit is not None:
            pending = pending[:limit]
        for offset in range(0, len(pending), chunk_size):
            chunk = pending[offset:offset + chunk_size]
            db.execute("BEGIN IMMEDIATE")
            try:
                if revision(db) != expected or file_identity(db_path) != plan["database_file_identity"]:
                    raise ValueError("pedigree-source-changed-replan-required")
                if _details_projection(db)[1] != plan["horse_details_projection_sha256"]:
                    raise ValueError("pedigree-horse-details-changed-replan-required")
                chunk_counts = Counter()
                for target in chunk:
                    row_id, race_id, horse_id = target["row_id"], target["race_id"], target["horse_id"]
                    saved = db.execute("SELECT race_id,data FROM race_results_ultimate WHERE id=?", (row_id,)).fetchone()
                    if not saved or saved[0] != race_id or digest(saved[1]) != target["before_sha256"]:
                        raise ValueError("pedigree-target-before-image-changed")
                    old = json.loads(saved[1])
                    header = db.execute("SELECT data FROM races_ultimate WHERE race_id=?", (race_id,)).fetchone()
                    info = json.loads(header[0]) if header else {}
                    day = normalized_date(info.get("date") or info.get("race_date"))
                    if (not is_jra_race_id(race_id) or day != target["race_date"] or not PERIOD[0] <= day <= PERIOD[1]
                            or old.get("horse_id") != horse_id or old.get("race_id", race_id) != race_id):
                        raise ValueError("pedigree-target-identity-or-period-conflict")
                    evidence = consensus.get(horse_id)
                    if evidence is None or digest(encode(evidence)) != target["consensus_sha256"]:
                        raise ValueError("pedigree-consensus-binding-invalid")
                    values, invalid = _names(old)
                    if invalid or any(values[field] - {evidence["pedigree"][field]} for field in FIELDS):
                        raise ValueError("pedigree-existing-alias-conflict")
                    fill = target["fill"]
                    if not fill or any(field not in FIELDS or _present(old.get(field)) or value != evidence["pedigree"][field] for field, value in fill.items()):
                        raise ValueError("pedigree-existing-value-overwrite-forbidden")
                    new = {**old, **fill}
                    after = encode(new)
                    prior_revision = revision(db)
                    updated = db.execute("UPDATE race_results_ultimate SET data=? WHERE id=? AND data=?", (after, row_id, saved[1]))
                    if updated.rowcount != 1:
                        raise ValueError("pedigree-target-conditional-update-failed")
                    after_revision = revision(db)
                    if after_revision[0] != prior_revision[0] or after_revision[1] != prior_revision[1] + 1:
                        raise ValueError("pedigree-source-revision-did-not-advance")
                    _journal(db, plan["batch_id"], "race_results_ultimate", row_id, race_id, saved[1], after,
                        {"repair_kind": "static_pedigree_gap", "plan_sha256": plan_sha,
                         "consensus_sha256": target["consensus_sha256"], "filled_fields": sorted(fill),
                         "source_revision_before": prior_revision, "source_revision_after": after_revision,
                         "observed_at_utc": now(), "source_fact_kind": "static_pedigree_from_saved_evidence",
                         "historical_capture_time_verified": False, "not_prestart_evidence": True})
                    expected = after_revision
                    chunk_counts["updated_rows"] += 1
                    for field in fill:
                        chunk_counts["filled_slots_" + field] += 1
                db.commit()
                counts.update(chunk_counts)
            except BaseException:
                db.rollback()
                raise
            time.sleep(pause_seconds)
        if revision(db) != expected or file_identity(db_path) != plan["database_file_identity"]:
            raise ValueError("pedigree-source-changed-after-apply")
        if _details_projection(db)[1] != plan["horse_details_projection_sha256"]:
            raise ValueError("pedigree-horse-details-changed-after-apply")
    if _cache_projection(cache_path)[1] != plan["cache_projection_sha256"]:
        raise ValueError("pedigree-cache-changed-during-apply")
    _check_code(plan["code_hashes"])
    if protected_check:
        protected_check(plan["protected_before"])
    result = {"schema": "saved-pedigree-gap-execution-v1", "batch_id": plan["batch_id"],
        "plan_sha256": plan_sha, "started_at_utc": started, "finished_at_utc": now(),
        "planned_source_revision": plan["source_revision"],
        "source_revision_before": execution_source_before, "source_revision_after": expected,
        "counts": dict(counts), "max_rows_per_transaction": chunk_size,
        "remaining_planned_rows": plan["counts"].get("planned_rows", 0) - len(done) - counts["updated_rows"],
        "network_requests": 0, "physical_database_copy_or_hash": False,
        "historical_prestart_capture_proven": False, "existing_nonempty_fields_preserved": True}
    json_write_new(run_dir / ("execution-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json"), result)
    return result
