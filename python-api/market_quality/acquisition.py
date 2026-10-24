"""Read-only, cohort-scoped acquisition evidence; never a model-readiness gate.

The three acquisition ledgers are authoritative. Legacy scraped_dates is only
compared for contradictions. Absent state is unproven, not inferred clean from
the presence of raw rows. This module does not inspect outcome performance,
repair source data, or certify payout/runner/PIT integrity.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


AUTHORITATIVE = (
    "scrape_date_completeness", "scrape_race_acquisition_state", "scrape_repair_queue",
)
TABLES = AUTHORITATIVE + (
    "scraped_dates", "races_ultimate", "race_results_ultimate", "scrape_race_observations",
)
CORE_FIELDS = ("odds", "popularity", "horse_weight", "finish_position", "finish_time")
ALLOWED_MISSING = {"pedigree"}


def _encoded(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_encoded(value)).hexdigest()


def _cohort(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"race_id", "race_date", "horse_id", "horse_number"}
    if not required <= set(frame):
        raise ValueError(f"cohort missing columns: {sorted(required - set(frame))}")
    out = frame[list(sorted(required))].copy()
    out["race_id"] = out.race_id.astype(str)
    out["race_date"] = out.race_date.astype(str)
    out["horse_id"] = out.horse_id.fillna("").astype(str)
    if out.empty or not out.race_id.str.fullmatch(r"\d{12}").all():
        raise ValueError("nonempty valid race IDs required")
    if not out.race_date.str.fullmatch(r"202[12]\d{4}").all():
        raise ValueError("only the original 2021/2022 research cohort is allowed")
    pd.to_datetime(out.race_date, format="%Y%m%d", errors="raise")
    if out.groupby("race_id").race_date.nunique().max() != 1:
        raise ValueError("race has contradictory dates")
    numbers = pd.to_numeric(out.horse_number, errors="coerce")
    if numbers.isna().any() or not ((numbers > 0) & (numbers % 1 == 0)).all():
        raise ValueError("positive integer runner numbers required")
    out["horse_number"] = numbers.astype(int)
    if out.duplicated(["race_id", "horse_number"]).any():
        raise ValueError("cohort runner key duplicated")
    return out.sort_values(["race_id", "horse_number"]).reset_index(drop=True)


def _select(conn: sqlite3.Connection, table: str, column: str, values: list[str],
            *, entity_type: str | None = None) -> list[dict]:
    # Table/column values are module constants, never caller-provided SQL.
    result = []
    for offset in range(0, len(values), 400):
        part = values[offset:offset + 400]
        query = f'SELECT * FROM "{table}" WHERE "{column}" IN ({",".join("?" for _ in part)})'
        args: list[Any] = list(part)
        if entity_type is not None:
            query += " AND entity_type=?"
            args.append(entity_type)
        result.extend(dict(row) for row in conn.execute(query, args))
    return sorted(result, key=lambda row: _encoded(row))


def read_scoped_evidence(db_path: Path, cohort: pd.DataFrame, *, immutable: bool) -> dict:
    """Capture an internally consistent read transaction; no DDL or source writes.

    immutable=True is only for an already finalized snapshot, never the live DB.
    Core results are counted by indexed race_id; parent audit owns runner bodies.
    """
    frame = _cohort(cohort)
    path = Path(db_path).resolve(strict=True)
    wal = Path(str(path) + "-wal")
    if immutable and wal.exists() and wal.stat().st_size:
        raise ValueError("immutable input must be a finalized snapshot without pending WAL")
    races = sorted(frame.race_id.unique())
    dates = sorted(frame.race_date.unique())
    horses = sorted(set(frame.horse_id) - {""})
    with sqlite3.connect(path.as_uri() + "?mode=ro" + ("&immutable=1" if immutable else ""),
                         uri=True, timeout=5) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        try:
            existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            schemas = {t: [dict(r) for r in conn.execute(f'PRAGMA table_info("{t}")')]
                       for t in TABLES if t in existing}
            extract: dict[str, Any] = {t: [] for t in TABLES}
            for t, key, ids in (
                (AUTHORITATIVE[0], "race_date", dates),
                (AUTHORITATIVE[1], "race_id", races),
                ("scraped_dates", "date", dates),
                ("races_ultimate", "race_id", races),
                ("scrape_race_observations", "race_id", races),
            ):
                if t in existing:
                    extract[t] = _select(conn, t, key, ids)
            if AUTHORITATIVE[2] in existing:
                extract[AUTHORITATIVE[2]] = sorted([
                    row for typ, ids in (("race", races), ("date", dates), ("horse", horses))
                    for row in _select(conn, AUTHORITATIVE[2], "entity_id", ids, entity_type=typ)
                ], key=lambda row: _encoded(row))
            if "race_results_ultimate" in existing:
                for offset in range(0, len(races), 400):
                    part = races[offset:offset + 400]
                    query = ('SELECT race_id, COUNT(*) AS result_row_count FROM race_results_ultimate '
                             f'WHERE race_id IN ({",".join("?" for _ in part)}) GROUP BY race_id')
                    extract["race_results_ultimate"].extend(dict(r) for r in conn.execute(query, part))
            extract["race_results_ultimate"].sort(key=lambda r: r["race_id"])
        finally:
            conn.rollback()
    return {
        "source_path": str(path), "source_is_immutable_snapshot": immutable,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "scope": {"races": len(races), "runners": len(frame), "dates": len(dates),
                  "date_min": min(dates), "date_max": max(dates),
                  "cohort_key_sha256": _digest(frame.to_dict("records"))},
        "schemas": schemas, "missing_tables": sorted(set(TABLES) - set(schemas)),
        "tables": extract, "scoped_evidence_sha256": _digest(extract),
        "result_body_policy": "counts only; full runner/payout integrity audited separately",
    }


def _json(value: Any, typ: type) -> Any:
    result = json.loads(value)
    if not isinstance(result, typ):
        raise ValueError("wrong JSON container")
    return result


def classify_acquisition(cohort: pd.DataFrame, evidence: dict) -> tuple[pd.DataFrame, dict]:
    """Separate authoritative acquisition proof from later research gates."""
    frame = _cohort(cohort)
    if evidence["scope"]["cohort_key_sha256"] != _digest(frame.to_dict("records")):
        raise ValueError("evidence belongs to a different cohort")
    tables = evidence["tables"]
    states = {r["race_id"]: r for r in tables[AUTHORITATIVE[1]]}
    dates = {r["race_date"]: r for r in tables[AUTHORITATIVE[0]]}
    raw = {r["race_id"]: r for r in tables["races_ultimate"]}
    counts = {r["race_id"]: r["result_row_count"] for r in tables["race_results_ultimate"]}
    legacy = {r["date"]: r for r in tables["scraped_dates"]}
    repairs: dict[tuple[str, str], list] = defaultdict(list)
    for repair in tables[AUTHORITATIVE[2]]:
        repairs[(repair["entity_type"], repair["entity_id"])].append(repair)
    rows = []
    for race_id, runners in frame.groupby("race_id", sort=True):
        date = runners.race_date.iloc[0]
        state, day = states.get(race_id), dates.get(date)
        errors, conflicts, allowed = [], [], []
        fields, daysets = {}, {}
        if evidence["missing_tables"] and any(t in evidence["missing_tables"] for t in AUTHORITATIVE):
            errors.append("authoritative_table_missing")
        if state is None:
            errors.append("race_state_missing")
        else:
            if state.get("race_date") != date:
                conflicts.append("race_state_date_mismatch")
            if state.get("lifecycle") != "settled":
                errors.append("lifecycle_not_settled")
            if state.get("quality_status") != "complete":
                errors.append("quality_status_not_complete")
            if not state.get("completed_at"):
                errors.append("completed_at_missing")
            else:
                try:
                    datetime.fromisoformat(state["completed_at"].replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    errors.append("completed_at_invalid")
            try:
                fields = _json(state.get("field_states_json"), dict)
                if _json(state.get("quality_errors_json"), list):
                    errors.append("required_quality_errors_present")
            except (ValueError, TypeError):
                errors.append("race_state_json_invalid")
            for name in CORE_FIELDS:
                if fields.get(name) != "available":
                    errors.append(f"core_field_unproven:{name}")
            for name in ALLOWED_MISSING:
                if fields.get(name) in {"repair_required", "source_missing", "partial"}:
                    allowed.append(name)
                elif fields.get(name) != "available":
                    errors.append(f"optional_field_state_unproven:{name}")
        if day is None:
            errors.append("date_state_missing")
        else:
            try:
                for name in ("expected", "saved", "complete", "missing", "quarantined"):
                    values = _json(day[f"{name}_race_ids_json"], list)
                    if any(not isinstance(x, str) or len(x) != 12 or not x.isdigit() for x in values):
                        raise ValueError("invalid race ID")
                    if len(values) != len(set(values)):
                        conflicts.append(f"date_{name}_ids_duplicate")
                    daysets[name] = set(values)
                if len(daysets["expected"]) != day["expected_race_count"]:
                    conflicts.append("date_expected_count_mismatch")
                if daysets["complete"] & (daysets["missing"] | daysets["quarantined"]):
                    conflicts.append("date_complete_missing_overlap")
                for name in ("expected", "saved", "complete"):
                    if race_id not in daysets[name]:
                        errors.append(f"race_not_in_date_{name}")
                if race_id in daysets["missing"] | daysets["quarantined"]:
                    errors.append("race_date_missing_or_quarantined")
                if day.get("status") == "complete" and daysets["expected"] - daysets["complete"]:
                    conflicts.append("date_complete_status_contradiction")
                if day.get("status") not in {"complete", "partial", "pending"}:
                    errors.append("date_status_not_usable")
            except (KeyError, ValueError, TypeError):
                errors.append("date_state_json_invalid")
        raw_race = raw.get(race_id)
        if raw_race is None:
            errors.append("canonical_race_absent")
        else:
            try:
                payload = _json(raw_race["data"], dict)
                if str(payload.get("race_id", "")) != race_id:
                    conflicts.append("canonical_race_id_mismatch")
                if str(payload.get("date", "")).replace("-", "").replace("/", "") != date:
                    conflicts.append("canonical_race_date_mismatch")
            except (KeyError, ValueError, TypeError):
                errors.append("canonical_race_json_invalid")
        if not counts.get(race_id):
            errors.append("canonical_results_absent")
        relevant = list(repairs[("race", race_id)]) + list(repairs[("date", date)])
        for horse in set(runners.horse_id) - {""}:
            relevant.extend(repairs[("horse", horse)])
        unresolved = [r for r in relevant if r.get("status") != "completed"]
        blockers = [r for r in unresolved if r.get("repair_kind") not in ALLOWED_MISSING]
        if blockers:
            errors.append("unresolved_required_repair")
        for repair in unresolved:
            if repair.get("repair_kind") in ALLOWED_MISSING:
                allowed.append(repair["repair_kind"])
        old = legacy.get(date)
        legacy_conflict = bool(old and (
            old.get("no_race") or day is None or day.get("status") != "complete"
            or old.get("race_count") != day.get("expected_race_count")
        ))
        excluded = bool(state and state.get("excluded_from_standard"))
        partial = bool(state and raw_race and counts.get(race_id))
        status = ("F_EXCLUDED" if excluded else "E_CONFLICT" if conflicts else
                  ("C_PARTIAL" if partial else "D_INCOMPLETE") if errors else
                  "B_CLEAN_WITH_ALLOWED_MISSING" if allowed else "A_CLEAN")
        rows.append({
            "race_id": race_id, "race_date": date, "cohort_runner_count": len(runners),
            "canonical_race_present": raw_race is not None,
            "canonical_result_count": counts.get(race_id, 0),
            "canonical_result_count_matches_cohort": counts.get(race_id, 0) == len(runners),
            "lifecycle": state.get("lifecycle") if state else None,
            "quality_status": state.get("quality_status") if state else None,
            "completed_at": state.get("completed_at") if state else None,
            "last_source_status": state.get("last_source_status") if state else None,
            "field_states_json": json.dumps(fields, ensure_ascii=False, sort_keys=True),
            "date_status": day.get("status") if day else None,
            "race_in_date_complete": race_id in daysets.get("complete", set()),
            "date_partial_outside_cohort": bool(day and day.get("status") == "partial"
                                                and race_id in daysets.get("complete", set())),
            "excluded_from_standard": excluded,
            "repair_queue_rows": len(relevant), "unresolved_repair_rows": len(unresolved),
            "required_unresolved_repair_rows": len(blockers),
            "legacy_present": old is not None, "legacy_conflict": legacy_conflict,
            "acquisition_classification": status, "acquisition_core_pass": not (excluded or errors or conflicts),
            "acquisition_pass": not (excluded or errors or conflicts),
            "allowed_missing": sorted(set(allowed)),
            "allowed_missing_json": json.dumps(sorted(set(allowed))),
            "issues_json": json.dumps(sorted(set(errors + conflicts))),
            "blockers_json": json.dumps(sorted(set(errors + conflicts + (["excluded_from_standard"] if excluded else [])))),
        })
    audit = pd.DataFrame(rows)
    summary = {
        "scope": evidence["scope"], "source_path": evidence["source_path"],
        "scoped_evidence_sha256": evidence["scoped_evidence_sha256"],
        "classification": audit.acquisition_classification.value_counts().to_dict(),
        "core_pass_races": int(audit.acquisition_core_pass.sum()),
        "legacy_conflict_races": int(audit.legacy_conflict.sum()),
        "result_count_mismatch_races": int((~audit.canonical_result_count_matches_cohort).sum()),
        "date_status": dict(Counter(r["status"] for r in tables[AUTHORITATIVE[0]])),
        "repair_status": dict(Counter("|".join(str(r[k]) for k in ("entity_type", "repair_kind", "status"))
                                      for r in tables[AUTHORITATIVE[2]])),
        "missing_tables": evidence["missing_tables"],
        "policy": {
            "authority": list(AUTHORITATIVE), "legacy_used_for_quality": False,
            "allowed_missing": sorted(ALLOWED_MISSING),
            "allowed_missing_gate": "pedigree is provisional only: independent missing-safe/strict-prior feature proof required",
            "partial_date": "race must individually be expected, saved and complete, not missing/quarantined",
            "scope_limit": "acquisition evidence only; not runner, payout, PIT, or MODEL_RESEARCH_READY proof",
            "horse_repair_limit": "horse repair applies conservatively, even if its last provenance names another race",
        },
    }
    return audit, summary


def audit_acquisition(snapshot_path: Path, cohort: pd.DataFrame, *,
                      live_db_path: Path | None = None, output_dir: Path | None = None) -> tuple[pd.DataFrame, dict]:
    evidence = read_scoped_evidence(snapshot_path, cohort, immutable=True)
    audit, summary = classify_acquisition(cohort, evidence)
    live = None
    if live_db_path is not None:
        live = read_scoped_evidence(live_db_path, cohort, immutable=False)
        current, current_summary = classify_acquisition(cohort, live)
        summary["live_comparison"] = {
            "path": live["source_path"], "captured_at": live["captured_at"],
            "same_scoped_evidence": evidence["scoped_evidence_sha256"] == live["scoped_evidence_sha256"],
            "same_acquisition_decisions": audit.equals(current),
            "scoped_evidence_sha256": live["scoped_evidence_sha256"],
            "classification": current_summary["classification"],
        }
    if output_dir is not None:
        output = Path(output_dir).resolve()
        if output == Path(snapshot_path).resolve().parent or output in Path(snapshot_path).resolve().parents:
            raise ValueError("output must be separate from source")
        output.mkdir(parents=True, exist_ok=True)
        names = ["acquisition_audit.csv", "acquisition_summary.json", "acquisition_sources.json"]
        if any((output / name).exists() for name in names):
            raise FileExistsError("acquisition artifacts already exist")
        # Exclusive outputs preserve prior runs; no source file is modified.
        with (output / names[0]).open("x", encoding="utf-8", newline="") as stream:
            audit.to_csv(stream, index=False)
        sources = {"snapshot": evidence, "live": live}
        with (output / names[2]).open("xb") as stream:
            stream.write(_encoded(sources))
        summary["artifacts"] = {
            name: {"path": str(output / name), "sha256": hashlib.sha256((output / name).read_bytes()).hexdigest()}
            for name in (names[0], names[2])
        }
        with (output / names[1]).open("xb") as stream:
            stream.write(_encoded(summary))
    return audit, summary
