"""Build an isolated, explicitly scoped experiment dataset from a snapshot.

Only compact canonical fields enter memory: large scraped JSON records are
decoded individually and discarded. No scraper feature engineering, original
prev fields, profile totals, or market-derived inputs are used.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import unicodedata
import uuid

import numpy as np
import pandas as pd

from training.eligibility import select_training_eligible_rows
from .features import ALLOWED_FEATURES, engineer_features


DATASET_SCHEMA_VERSION = "experiment-jra-flat-dataset-v2"
JRA_VENUES = {"01": "札幌", "02": "函館", "03": "福島", "04": "新潟",
              "05": "東京", "06": "中山", "07": "中京", "08": "京都",
              "09": "阪神", "10": "小倉"}
SURFACES = {"芝": "turf", "turf": "turf", "ダート": "dirt", "ダ": "dirt", "dirt": "dirt"}
# A deliberately conservative experiment scope, not an exhaustive official
# course validator. Legacy rows can mislabel jumping as turf/dirt. Both named
# jumping evidence and unsupported distances are quarantined, including from
# the pre-2016 history. A missing/ambiguous type is never inferred from speed.
FLAT_DISTANCE_CONTRACT = {
    "turf": frozenset({1000, 1200, 1400, 1500, 1600, 1700, 1800, 2000, 2200,
                       2300, 2400, 2500, 2600, 3000, 3200, 3400, 3600}),
    "dirt": frozenset({1000, 1150, 1200, 1300, 1400, 1600, 1700, 1800, 1900,
                       2000, 2100, 2300, 2400, 2500}),
}
DOMAIN_FIELDS = ("race_name", "race_class", "race_grade", "grade", "track_type", "surface",
                 "race_type", "course_type", "course", "course_info", "race_condition", "race_conditions")
JUMP_MARKER = re.compile(r"障|ジャンプ|jump|steeple|j[\s.・･\-]*g|j[\s.・･\-]*s(?=\W|$)", re.IGNORECASE)
SOURCE_COLUMNS = ["race_id", "horse_id", "horse_number", "race_date", "finish",
                  "time_seconds", "distance", "surface", "venue", "num_horses",
                  "odds", "popularity", "bracket_number", "age", "sex", "burden_weight",
                  "horse_weight", "horse_weight_change", "field_condition", "weather",
                  "race_class", "jockey_id", "trainer_id", "sire", "damsire", "last_3f_time"]
EVALUATION_COLUMNS = ["race_id", "horse_id", "horse_number", "race_date", "finish",
                      "time_seconds", "distance", "surface", "venue", "num_horses", "odds",
                      "popularity", "tansho_payout"]


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _json_digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _text(value) -> str | None:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return None
    text = str(value).strip()
    return None if text.lower() in {"", "none", "nan", "<na>", "unknown", "unknown_local",
                                   "null", "n/a", "-", "--", "不明"} else text


def _number(value) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else math.nan
    except (ValueError, TypeError):
        return math.nan


def _first(row: dict, *names):
    for name in names:
        value = row.get(name)
        if _text(value) is not None:
            return value
    return None


def _date(value) -> str | None:
    text = _text(value)
    if not text or not re.fullmatch(r"\d{8}|\d{4}-\d{2}-\d{2}|\d{4}/\d{2}/\d{2}", text):
        return None
    compact = re.sub(r"\D", "", text)
    try:
        parsed = date(int(compact[:4]), int(compact[4:6]), int(compact[6:8]))
    except ValueError:
        return None
    return parsed.strftime("%Y%m%d")


def _time(value) -> float:
    text = _text(value)
    if not text:
        return math.nan
    if re.fullmatch(r"\d+:[0-5]?\d(?:\.\d+)?", text):
        minutes, seconds = text.split(":")
        return float(minutes) * 60 + float(seconds)
    return _number(text)


def _venue(value) -> str | None:
    text = _text(value)
    return JRA_VENUES.get(text.zfill(2), text) if text else None


def _race_metadata(race_id: str, row: dict) -> tuple[dict | None, str | None]:
    if not re.fullmatch(r"\d{12}", race_id) or race_id[4:6] not in JRA_VENUES:
        return None, "outside_jra"
    day = _date(_first(row, "date", "race_date"))
    if not day or race_id[:4] != day[:4]:
        return None, "race_date_invalid_or_inconsistent"
    if any(_text(row.get(key)) is not None and _date(row[key]) != day for key in ("date", "race_date")):
        return None, "race_date_invalid_or_inconsistent"
    if day < "20130101":
        return None, "before_history_start"
    if _text(row.get("race_id")) not in (None, race_id):
        return None, "race_identity_conflict"
    surface = SURFACES.get(_text(_first(row, "track_type", "surface")))
    domain_values = [unicodedata.normalize("NFKC", str(row.get(key) or "")) for key in DOMAIN_FIELDS]
    if any(JUMP_MARKER.search(value) for value in domain_values):
        return None, "jump_evidence_quarantined"
    if (not surface
            or any(_text(row.get(key)) is not None and SURFACES.get(_text(row[key])) != surface
                   for key in ("track_type", "surface"))):
        return None, "not_confirmed_flat_turf_dirt"
    venue = _venue(row.get("venue"))
    if venue != JRA_VENUES[race_id[4:6]]:
        return None, "venue_identity_unconfirmed"
    distance, count = _number(row.get("distance")), _number(row.get("num_horses"))
    if row.get("_invalid_distance") or not distance > 0 or not count > 0 or count != int(count):
        return None, "required_race_metadata_invalid"
    if distance not in FLAT_DISTANCE_CONTRACT[surface]:
        return None, "outside_conservative_flat_distance_contract"
    return {"race_id": race_id, "race_date": day, "surface": surface,
            "venue": venue, "distance": distance, "num_horses": count,
            **{key: _text(row.get(key)) for key in ("field_condition", "weather", "race_class")}}, None


def _runner(race_id: str, row: dict, meta: dict) -> tuple[dict | None, str | None]:
    if _text(row.get("race_id")) not in (None, race_id):
        return None, "runner_race_identity_conflict"
    for key in ("race_date", "date"):
        if _text(row.get(key)) is not None and _date(row[key]) != meta["race_date"]:
            return None, "runner_race_date_conflict"
    for key in ("distance", "num_horses"):
        if _text(row.get(key)) is not None and _number(row[key]) != meta[key]:
            return None, "runner_race_metadata_conflict"
    if _text(row.get("venue")) is not None and _venue(row["venue"]) != meta["venue"]:
        return None, "runner_race_metadata_conflict"
    for key in ("surface", "track_type"):
        if _text(row.get(key)) is not None and SURFACES.get(_text(row[key])) != meta["surface"]:
            return None, "runner_race_metadata_conflict"
    result = dict(meta)
    result["horse_id"] = _text(row.get("horse_id")) or ""
    result["horse_number"] = _number(row.get("horse_number"))
    number = result["horse_number"]
    if not (math.isfinite(number) and 1 <= number <= 18 and number == int(number)):
        return None, "runner_number_invalid"
    result["finish"] = _number(_first(row, "finish", "finish_position"))
    result["time_seconds"] = _time(_first(row, "time_seconds", "finish_time", "time"))
    for target, aliases in {
        "odds": ("odds",), "popularity": ("popularity",),
        "bracket_number": ("bracket_number",), "age": ("age",),
        "burden_weight": ("burden_weight", "jockey_weight"),
        "horse_weight": ("horse_weight", "weight_kg"),
        "horse_weight_change": ("horse_weight_change", "weight_change"),
        "last_3f_time": ("last_3f_time", "last_3f"),
    }.items():
        result[target] = _number(_first(row, *aliases))
    result["sex"] = _text(row.get("sex"))
    sex_age = re.fullmatch(r"(牡|牝|セ|セン)(\d+)", str(row.get("sex_age") or ""))
    if sex_age:
        if not result["sex"]:
            result["sex"] = sex_age.group(1)
        if not math.isfinite(result["age"]):
            result["age"] = float(sex_age.group(2))
    for target, aliases in {"jockey_id": ("jockey_id",), "trainer_id": ("trainer_id",),
                            "sire": ("sire", "sire_name"),
                            "damsire": ("damsire", "dam_sire_name")}.items():
        result[target] = _text(_first(row, *aliases))
    return result, None


def _load_compact(connection: sqlite3.Connection) -> tuple[pd.DataFrame, dict, dict, dict]:
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"races_ultimate", "race_results_ultimate"}.issubset(tables):
        raise ValueError("experiment-source-tables-missing")
    # Provider-specific identifier/precedence rules need a separate contract.
    # Never quietly train stale legacy duplicates when a sidecar is present.
    for table in ("licensed_history_entries", "official_history_entries"):
        if table in tables and connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
            raise ValueError("experiment-sidecar-source-not-supported")
    metadata, scope_counts, invalid_races = {}, Counter(), {}
    source_races = 0
    for race_id, payload in connection.execute("SELECT race_id,data FROM races_ultimate"):
        source_races += 1
        try:
            row = json.loads(payload)
            if not isinstance(row, dict):
                raise ValueError("object-required")
            meta, reason = _race_metadata(str(race_id), row)
        except (ValueError, TypeError):
            meta, reason = None, "race_json_invalid"
        if reason:
            scope_counts[reason] += 1
        else:
            metadata[str(race_id)] = meta
    columns = {name: [] for name in SOURCE_COLUMNS}
    scanned_rows, candidate_rows = 0, Counter()
    # JSON is streamed, not fetchall'ed and not expanded into arbitrary columns.
    query = ("SELECT race_id,data FROM race_results_ultimate "
             "WHERE substr(race_id,5,2) BETWEEN '01' AND '10' "
             "AND substr(race_id,1,4)>='2013'")
    for race_id, payload in connection.execute(query):
        scanned_rows += 1
        race_id = str(race_id)
        if race_id not in metadata:
            continue
        candidate_rows[race_id] += 1
        try:
            row = json.loads(payload)
            if not isinstance(row, dict):
                raise ValueError("object-required")
            compact, reason = _runner(race_id, row, metadata[race_id])
        except (ValueError, TypeError):
            compact, reason = None, "runner_json_invalid"
        if reason:
            invalid_races.setdefault(race_id, reason)
            continue
        for name in SOURCE_COLUMNS:
            columns[name].append(compact.get(name))
    frame = pd.DataFrame(columns)
    if frame.empty:
        raise ValueError("experiment-scope-empty")
    invalid_counts = Counter(invalid_races.values())
    invalid_row_counts = Counter()
    for race_id, reason in invalid_races.items():
        invalid_row_counts[reason] += candidate_rows[race_id]
    frame = frame.loc[~frame["race_id"].isin(invalid_races)].reset_index(drop=True)
    quality = {}
    if "scrape_race_acquisition_state" in tables:
        for rid, lifecycle, status, excluded in connection.execute(
            "SELECT race_id,lifecycle,quality_status,excluded_from_standard FROM scrape_race_acquisition_state"
        ):
            if str(rid) in metadata:
                quality[str(rid)] = (str(lifecycle or ""), str(status or ""), bool(excluded))
    payouts, payout_conflicts = {}, set()
    if "return_tables_ultimate" in tables:
        for rid, combination, value in connection.execute(
            "SELECT race_id,combinations,payout FROM return_tables_ultimate WHERE bet_type='単勝'"
        ):
            if str(rid) not in metadata or not re.fullmatch(r"\d{1,2}", str(combination).strip()):
                continue
            amount = _number(value)
            if not amount > 0:
                continue
            key = (str(rid), int(str(combination).strip()))
            if key in payouts and payouts[key] != amount:
                payout_conflicts.add(key)
            else:
                payouts[key] = amount
    for key in payout_conflicts:
        payouts.pop(key, None)
    audit = {"source_races": source_races, "streamed_jra_runner_rows": scanned_rows,
             "scope_candidate_races": len(metadata), "scope_candidate_rows": sum(candidate_rows.values()),
             "scope_excluded_races": dict(scope_counts),
             "integrity_exclusions": {reason: {"races": count, "rows": invalid_row_counts[reason]}
                                      for reason, count in invalid_counts.items()},
             "conflicting_win_payout_entries": len(payout_conflicts),
             "source_columns_loaded": SOURCE_COLUMNS.copy()}
    return frame, quality, payouts, audit


def build_dataset(snapshot_path: Path, output_dir: Path) -> dict:
    """Write dataset.parquet and dataset_manifest.json only in output_dir.

    The source must be an immutable SQLite snapshot with no pending WAL. Output
    files are new, immutable experiment artifacts and are never overwritten.
    """
    source = Path(snapshot_path)
    if source.is_symlink() or not source.is_file():
        raise ValueError("experiment-snapshot-invalid")
    source = source.resolve(strict=True)
    wal = Path(str(source) + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise ValueError("experiment-snapshot-has-pending-wal")
    output = Path(output_dir)
    if output.is_symlink():
        raise ValueError("experiment-output-symlink")
    output.mkdir(parents=True, exist_ok=True)
    output = output.resolve(strict=True)
    dataset_path, manifest_path = output / "dataset.parquet", output / "dataset_manifest.json"
    if dataset_path.exists() or manifest_path.exists():
        raise FileExistsError("experiment-dataset-already-exists")
    source_digest = _digest(source)
    connection = sqlite3.connect(source.as_uri() + "?mode=ro&immutable=1", uri=True, timeout=5)
    try:
        connection.execute("PRAGMA query_only=ON")
        frame, quality, payouts, source_audit = _load_compact(connection)
    finally:
        connection.close()
    selection = select_training_eligible_rows(frame, target="speed_deviation", recorded_quality_states=quality)
    eligibility = selection.manifest.as_dict()
    raw_counts = frame.groupby("race_id", sort=False).size()
    selected_counts = selection.frame.groupby("race_id", sort=False).size()
    partial_races = selected_counts.index[selected_counts.ne(raw_counts.reindex(selected_counts.index))]
    clean = selection.frame.loc[~selection.frame["race_id"].isin(partial_races)].reset_index(drop=True)
    cohort_exclusion = {"races": len(partial_races),
                        "rows": int(raw_counts.reindex(partial_races).sum())}
    del frame, selection
    if clean.empty:
        raise ValueError("experiment-complete-cohort-empty")
    history_rows, history_races = len(clean), int(clean["race_id"].nunique())
    engineered, feature_manifest = engineer_features(clean)
    del clean
    selected = engineered.loc[engineered["race_date"].ge("20160101")].copy()
    del engineered
    if selected.empty:
        raise ValueError("experiment-supervised-cohort-empty")
    selected["tansho_payout"] = [
        payouts.get((str(rid), int(number)), math.nan) if finish == 1 and math.isfinite(number) else math.nan
        for rid, number, finish in zip(selected["race_id"], selected["horse_number"], selected["finish"])
    ]
    feature_columns = feature_manifest["feature_sets"]["M03"]
    selected = selected[EVALUATION_COLUMNS + feature_columns].reset_index(drop=True)
    if set(feature_columns) != ALLOWED_FEATURES:
        raise ValueError("experiment-feature-contract-invalid")
    # Stable strings preserve identity and calendar semantics in parquet.
    for name in ("race_id", "horse_id", "race_date", "surface", "venue"):
        selected[name] = selected[name].astype("string")
    if _digest(source) != source_digest:
        raise ValueError("experiment-snapshot-mutated")
    temp_dataset = output / f".dataset-{uuid.uuid4().hex}.parquet.tmp"
    temp_manifest = output / f".dataset-manifest-{uuid.uuid4().hex}.json.tmp"
    try:
        selected.to_parquet(temp_dataset, index=False, engine="pyarrow", compression="zstd")
        dataset_digest = _digest(temp_dataset)
        final_ids = sorted(f"{rid}|{horse}|{number}" for rid, horse, number in zip(
            selected["race_id"], selected["horse_id"], selected["horse_number"]))
        final_eligibility_hash = _json_digest({"eligibility": eligibility["eligibility_sha256"],
                                              "cohort_exclusion": cohort_exclusion,
                                              "selected_row_ids_sha256": _json_digest(final_ids)})
        exclusions = dict(eligibility["exclusion_reason_counts"])
        exclusions.update(source_audit["integrity_exclusions"])
        exclusions["complete_runner_target_cohort"] = cohort_exclusion
        manifest = {
            "schema_version": 1, "dataset_schema_version": DATASET_SCHEMA_VERSION,
            "snapshot_sha256": source_digest, "dataset_sha256": dataset_digest,
            "dataset_file": "dataset.parquet", "dataset_path": str(dataset_path),
            "scope": {"jurisdiction": "JRA", "race_type": "flat", "surfaces": ["turf", "dirt"],
                      "supervised_start": "20160101", "history_start": "20130101",
                      "distance_contract": {key: sorted(values) for key, values in FLAT_DISTANCE_CONTRACT.items()},
                      "domain_policy": "Conservative experiment scope, not exhaustive official flat-course certification; jump markers or unsupported distances quarantined before all history generation.",
                      "source_policy": "ultimate_tables_only; nonempty sidecars fail closed"},
            "row_count": len(selected), "race_count": int(selected["race_id"].nunique()),
            "history_row_count": history_rows, "history_race_count": history_races,
            "date_from": str(selected["race_date"].min()), "date_to": str(selected["race_date"].max()),
            "payout_scope": "winner_only", "payout_unit": "JPY per 100 JPY win ticket",
            "payout_unknown_policy": "NaN; never substitute final odds for official payout",
            "known_winner_payout_rows": int(selected["tansho_payout"].notna().sum()),
            "cohort_policy": "All stored declared runners require confirmed finish and positive time; partial target races excluded in full. This is selected-cohort evaluation, not all-race operational performance.",
            "training_eligibility": eligibility, "eligibility_sha256": final_eligibility_hash,
            "exclusion_reason_counts": exclusions,
            "source_audit": source_audit, **feature_manifest,
            "source_contract": {"schema_version": 1, "input": "immutable SQLite snapshot",
                                "tables": ["races_ultimate", "race_results_ultimate"],
                                "row_fields": SOURCE_COLUMNS.copy(),
                                "history_boundary": "source_race_date < target_race_date",
                                "outcomes_and_market": "evaluation only; no original prev or current profile totals",
                                "snapshot_sha256": source_digest,
                                "dataset_module_sha256": _digest(Path(__file__)),
                                "features_module_sha256": _digest(Path(__file__).with_name("features.py"))},
            "exclusion_reason_counts_are_nonexclusive": True,
            "missingness": {name: {"missing": int(selected[name].isna().sum()),
                                     "fraction": float(selected[name].isna().mean())}
                            for name in selected.columns},
            "evaluation_only_columns": EVALUATION_COLUMNS.copy(),
            "market_features_excluded": True,
        }
        temp_manifest.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                                            indent=2, allow_nan=False) + "\n", encoding="utf-8")
        # Hard links are an exclusive publication: a concurrent builder cannot
        # replace another builder's completed output. Temp files stay in scope.
        import os
        os.link(temp_dataset, dataset_path)
        try:
            os.link(temp_manifest, manifest_path)
        except BaseException:
            dataset_path.unlink()
            raise
        return manifest
    finally:
        temp_dataset.unlink(missing_ok=True)
        temp_manifest.unlink(missing_ok=True)
