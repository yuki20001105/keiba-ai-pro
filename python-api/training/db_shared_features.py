"""New, read-only train/serve contract for the canonical 47 DB features.

The frozen experiment/forward implementations are reused, never modified.
Only complete eligible JRA flat races form observed history. This is not a
claim of complete career coverage or immutable pre-start capture provenance.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import date
import calendar
import hashlib
import json
from pathlib import Path
import re
import sqlite3

import numpy as np
import pandas as pd

from experiments import dataset as canonical
from experiments import features as frozen
from experiments import search_features
from experiments import training as frozen_training
from forward_adapters import feature_state
from training import eligibility


FEATURE_VERSION = "local-db-shared-v1"
FEATURE_COLUMNS = list(frozen.FEATURE_GROUPS["base"]) + list(frozen.HISTORY_COLUMNS)
CATEGORICAL_COLUMNS = list(frozen.BASE_CATEGORICAL)
FoldEncoder = frozen_training.FoldEncoder
_BATCH = 400
_KEYS = ["race_id", "horse_number"]
_OUTCOMES = {"finish", "time_seconds", "last_3f_time", "odds", "popularity"}
_CARD_FIELDS = set(canonical.SOURCE_COLUMNS) - _OUTCOMES | {
    "date", "track_type", "sex_age", "jockey_weight", "weight_kg",
    "weight_change", "weight_diff", "sire_name", "dam_sire_name",
}


def _source_module_hashes() -> dict[str, str]:
    modules = (canonical, frozen, search_features, frozen_training, feature_state, eligibility)
    return {module.__name__: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
            for module in modules} | {
        "training.db_shared_features": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }


_IMPORTED_SOURCE_HASHES = _source_module_hashes()


def source_hashes() -> dict[str, str]:
    """Bind loaded formula dependencies, separately from source-row provenance.

    A long-lived worker must not attest newly edited disk bytes while executing
    its older imported formula. Updating this contract requires a fresh import.
    """
    current = _source_module_hashes()
    if current != _IMPORTED_SOURCE_HASHES:
        raise ValueError("shared-feature-source-changed-since-import-restart-required")
    return dict(current)


def _period_date(value, *, end: bool) -> str:
    if value in (None, ""):
        return date.today().strftime("%Y%m%d") if end else "20160101"
    text = str(value).strip()
    if re.fullmatch(r"\d{6}|\d{4}-\d{2}", text):
        compact = text.replace("-", "")
        year, month = int(compact[:4]), int(compact[4:])
        try:
            return date(year, month, calendar.monthrange(year, month)[1] if end else 1).strftime("%Y%m%d")
        except (ValueError, calendar.IllegalMonthError) as error:
            raise ValueError("shared-period-date-invalid") from error
    result = canonical._date(text)
    if result is None:
        raise ValueError("shared-period-date-invalid")
    return result


def _batches(values):
    for offset in range(0, len(values), _BATCH):
        yield values[offset:offset + _BATCH]


@contextmanager
def _database(db_path):
    connection = sqlite3.connect(Path(db_path).resolve(strict=True).as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"races_ultimate", "race_results_ultimate"}.issubset(tables):
            raise ValueError("shared-source-tables-missing")
        for table in ("licensed_history_entries", "official_history_entries"):
            if table in tables and connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                raise ValueError("shared-sidecar-source-contract-not-supported")
        yield connection, tables
    finally:
        connection.close()


def _object(payload):
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("source-object-required")
    return value


def _weight_alias(raw: dict) -> dict:
    """New contract only: weight_diff is a day-card weight-change alias.

    Conflicting aliases are not guessed. This does not patch frozen models or
    substitute an unpublished change with zero.
    """
    value = dict(raw)
    supplied = [canonical._number(value[name]) for name in
                ("horse_weight_change", "weight_change", "weight_diff")
                if canonical._text(value.get(name)) is not None]
    if supplied and (not all(np.isfinite(supplied)) or len(set(supplied)) != 1):
        raise ValueError("shared-weight-change-alias-invalid-or-conflicting")
    if supplied:
        value["horse_weight_change"] = supplied[0]
    return value


def _metadata(connection, ids, audit):
    metadata, seen = {}, Counter()
    for batch in _batches(sorted(ids)):
        marks = ",".join("?" for _ in batch)
        for rid, payload in connection.execute(f"SELECT race_id,data FROM races_ultimate WHERE race_id IN ({marks})", batch):
            rid = str(rid)
            seen[rid] += 1
            try:
                meta, reason = canonical._race_metadata(rid, _object(payload))
            except (ValueError, TypeError):
                meta, reason = None, "race_json_invalid"
            if reason:
                audit["quarantined_races"][rid] = reason
            else:
                metadata[rid] = meta
    for rid in ids:
        if seen[rid] != 1:
            audit["quarantined_races"][rid] = "race_metadata_duplicate_or_missing"
        if rid in audit["quarantined_races"]:
            metadata.pop(rid, None)
    return metadata


def _target_metadata(connection, lower, upper, audit):
    # Only metadata (not 949k runner JSON rows) is scanned for the chosen years.
    ids = set()
    for rid, payload in connection.execute(
        "SELECT race_id,data FROM races_ultimate WHERE substr(race_id,1,4) BETWEEN ? AND ? "
        "AND substr(race_id,5,2) BETWEEN '01' AND '10'", (lower[:4], upper[:4]),
    ):
        audit["metadata_rows_scanned"] += 1
        rid = str(rid)
        try:
            meta, reason = canonical._race_metadata(rid, _object(payload))
        except (ValueError, TypeError):
            meta, reason = None, "race_json_invalid"
        if reason:
            audit["metadata_scope_exclusions"][reason] += 1
        elif lower <= meta["race_date"] <= upper:
            ids.add(rid)
    return _metadata(connection, ids, audit)


def _load_complete(connection, tables, metadata, audit):
    rows, quality = [], {}
    counts = Counter()
    for batch in _batches(sorted(metadata)):
        marks = ",".join("?" for _ in batch)
        for rid, payload in connection.execute(f"SELECT race_id,data FROM race_results_ultimate WHERE race_id IN ({marks})", batch):
            audit["runner_rows_loaded"] += 1
            rid = str(rid)
            counts[rid] += 1
            try:
                parsed, reason = canonical._runner(rid, _weight_alias(_object(payload)), metadata[rid])
            except (ValueError, TypeError):
                parsed, reason = None, "runner_json_or_alias_invalid"
            if reason:
                audit["quarantined_races"][rid] = reason
            else:
                rows.append(parsed)
        if "scrape_race_acquisition_state" in tables:
            for rid, lifecycle, status, excluded in connection.execute(
                "SELECT race_id,lifecycle,quality_status,excluded_from_standard "
                f"FROM scrape_race_acquisition_state WHERE race_id IN ({marks})", batch,
            ):
                if str(rid) in quality:
                    raise ValueError("shared-quality-ledger-duplicate")
                quality[str(rid)] = (str(lifecycle or ""), str(status or ""), bool(excluded))
    for rid in metadata:
        if counts[rid] == 0:
            audit["quarantined_races"][rid] = "runner_rows_missing"
    frame = pd.DataFrame(rows, columns=canonical.SOURCE_COLUMNS)
    frame = frame.loc[~frame.race_id.isin(audit["quarantined_races"])].reset_index(drop=True)
    if frame.empty:
        return frame, {"eligible_rows": 0, "eligible_races": 0, "partial_races_excluded": 0}
    selection = eligibility.select_training_eligible_rows(frame, target="speed_deviation", recorded_quality_states=quality)
    original = frame.groupby("race_id").size()
    selected = selection.frame.groupby("race_id").size()
    partial = selected.index[selected.ne(original.reindex(selected.index))]
    clean = selection.frame.loc[~selection.frame.race_id.isin(partial)].reset_index(drop=True)
    feature_state._cohort(clean, historical=True)
    return clean.reindex(columns=canonical.SOURCE_COLUMNS), {
        "eligible_rows": len(clean), "eligible_races": int(clean.race_id.nunique()),
        "partial_races_excluded": len(partial), "eligibility": selection.manifest.as_dict(),
    }


def _related_ids(connection, horses, upper, audit):
    if not horses:
        audit["horse_lookup"] = "no_nonempty_horse_ids"
        return set()
    probe = "SELECT DISTINCT race_id FROM race_results_ultimate WHERE json_extract(data,'$.horse_id') IN (?)"
    plans = [str(row[-1]) for row in connection.execute("EXPLAIN QUERY PLAN " + probe, (horses[0],))]
    indexed = any("SEARCH" in plan and "<expr>" in plan for plan in plans)
    matched = set()
    if indexed:
        audit["horse_lookup"] = "indexed_batched_json_horse_id"
        for batch in _batches(horses):
            marks = ",".join("?" for _ in batch)
            try:
                cursor = connection.execute(
                    "SELECT DISTINCT race_id FROM race_results_ultimate "
                    f"WHERE json_extract(data,'$.horse_id') IN ({marks})", batch,
                )
                matched.update(str(row[0]) for row in cursor)
            except sqlite3.OperationalError as error:
                raise ValueError("shared-history-horse-index-source-invalid") from error
    else:
        # One compact streaming fallback, not one full scan per horse/batch and
        # not a full runner DataFrame. No source index is created or changed.
        audit["horse_lookup"] = "single_compact_stream_no_horse_expression_index"
        selected = set(horses)
        for rid, horse in connection.execute(
            "SELECT race_id,CASE WHEN json_valid(data) THEN json_extract(data,'$.horse_id') END "
            "FROM race_results_ultimate WHERE substr(race_id,1,4) BETWEEN '2013' AND ? "
            "AND substr(race_id,5,2) BETWEEN '01' AND '10'", (upper[:4],),
        ):
            audit["horse_lookup_rows_streamed"] += 1
            if canonical._text(horse) in selected:
                matched.add(str(rid))
    return {rid for rid in matched if re.fullmatch(r"\d{12}", rid)
            and "2013" <= rid[:4] <= upper[:4] and rid[4:6] in canonical.JRA_VENUES}


def _audit():
    return {"quarantined_races": {}, "metadata_scope_exclusions": Counter(),
            "metadata_rows_scanned": 0, "runner_rows_loaded": 0,
            "horse_lookup_rows_streamed": 0}


def _load_history(connection, tables, current, upper, audit, *, strict, reuse_target_ids=None):
    horses = sorted({value for value in current.horse_id.map(canonical._text) if value})
    ids = _related_ids(connection, horses, upper, audit)
    metadata = _metadata(connection, ids, audit)
    date_safe = {rid: meta for rid, meta in metadata.items()
            if meta["race_date"] < upper or (not strict and meta["race_date"] == upper)}
    audit["same_day_or_future_races_excluded"] = len(metadata) - len(date_safe)
    reused = set(reuse_target_ids or ())
    safe = {rid: meta for rid, meta in date_safe.items() if rid not in reused}
    audit["selected_target_races_reused_without_runner_reload"] = len(date_safe) - len(safe)
    return _load_complete(connection, tables, safe, audit)


def load_db_frames(db_path, date_from=None, date_to=None):
    """Load chosen complete targets and horse-related complete history, RO.

    History includes selected-period results for subsequent days, not as own
    inputs. Its coverage is observed eligible history, never full career proof.
    """
    lower, upper = _period_date(date_from, end=False), _period_date(date_to, end=True)
    if lower < "20130101" or lower > upper:
        raise ValueError("shared-period-outside-contract")
    audit = _audit()
    with _database(db_path) as (connection, tables):
        targets, target_audit = _load_complete(connection, tables, _target_metadata(connection, lower, upper, audit), audit)
        history, history_audit = _load_history(connection, tables, targets, upper, audit, strict=False,
                                             reuse_target_ids=set(targets.race_id))
        # A complete target whose horse IDs are all missing still has verified
        # outcomes. Retain selected targets explicitly, not just those found
        # through a horse lookup, for later-day chronological construction.
        history = pd.concat([history.loc[~history.race_id.isin(set(targets.race_id))], targets], ignore_index=True)
        history_audit = {**history_audit, "eligible_rows": len(history),
                         "eligible_races": int(history.race_id.nunique()),
                         "selected_target_races_retained": int(targets.race_id.nunique())}
    audit["metadata_scope_exclusions"] = dict(audit["metadata_scope_exclusions"])
    coverage = {**audit, "feature_version": FEATURE_VERSION, "date_from": lower, "date_to": upper,
                "target": target_audit, "history": history_audit,
                "source": "ultimate_tables_readonly_single_snapshot_complete_related_races",
                "history_policy": "2013 start; observed eligible history; source date strictly before each target",
                "complete_career_coverage_verified": False, "source_hashes": source_hashes()}
    return targets, history, coverage


def build_training_features(target_frame: pd.DataFrame, history_frame: pd.DataFrame) -> pd.DataFrame:
    """Raw fixed-47 features, one chronological pass, caller index/order kept.

    Current outcome columns are discarded. Confirmed results from the explicit
    history frame may enter history only for subsequent dates. For an outcome-
    free last-day card the frozen day-batched builder requires internal dummy
    labels; they cannot reach any target because no later target day exists.
    """
    if not isinstance(target_frame, pd.DataFrame) or target_frame.columns.duplicated().any():
        raise ValueError("shared-target-frame-required")
    if target_frame.empty:
        return pd.DataFrame(index=target_frame.index, columns=FEATURE_COLUMNS)
    # Optional basics have canonical NaNs, not invented zeroes or legacy prev_*.
    target = target_frame.reindex(columns=canonical.SOURCE_COLUMNS).copy()
    feature_state._cohort(target, historical=False)
    target["horse_number"] = pd.to_numeric(target.horse_number).astype(int)
    target["horse_id"] = target.horse_id.map(frozen._identity).fillna("")
    history = history_frame.reindex(columns=canonical.SOURCE_COLUMNS).copy()
    checked = feature_state._cohort(history, historical=True)
    final_day = str(target.race_date.max())
    if not checked.empty and (checked.race_date.lt("20130101").any() or checked.race_date.gt(final_day).any()):
        raise ValueError("shared-history-outside-canonical-target-boundary")
    # Resolve confirmed results by identity; never use target outcome values.
    confirmed = checked.set_index(_KEYS)
    keys = pd.MultiIndex.from_frame(target[_KEYS])
    matched = keys.isin(confirmed.index)
    observations = confirmed.reindex(keys).reset_index(drop=True)
    replacements = target.copy()
    replacements[["finish", "time_seconds", "last_3f_time", "odds", "popularity"]] = np.nan
    for name in ("horse_id", "race_date", "distance", "surface", "venue", "num_horses"):
        left, right = observations[name], target[name].reset_index(drop=True)
        equal = (pd.to_numeric(left).eq(pd.to_numeric(right)) if name in {"distance", "num_horses"}
                 else left.astype("string").eq(right.astype("string")))
        if not equal.loc[matched].fillna(False).all():
            raise ValueError("shared-target-history-identity-or-metadata-conflict")
    if target.race_date.iloc[np.flatnonzero(~matched)].ne(final_day).any():
        raise ValueError("shared-intermediate-target-confirmed-history-required")
    for name in ("finish", "time_seconds", "last_3f_time"):
        values = observations[name].to_numpy(copy=True)
        if name == "finish":
            values[~matched] = target.horse_number.to_numpy()[~matched]
        elif name == "time_seconds":
            values[~matched] = 1.0
        replacements[name] = values
    remaining = checked.loc[~checked.race_id.isin(set(target.race_id))]
    combined = pd.concat([remaining, replacements], ignore_index=True)
    engineered, manifest = frozen.engineer_features(combined)
    result = engineered.set_index(_KEYS).loc[keys, FEATURE_COLUMNS].copy()
    result.index = target_frame.index.copy()
    result.attrs.update(feature_version=FEATURE_VERSION, source_hashes=source_hashes(),
                        current_outcomes_consumed=False, same_day_or_future_history_used=False,
                        point_in_time_audit=manifest["point_in_time_audit"],
                        complete_career_coverage_verified=False)
    return result


def _current_card(race_info, horses):
    if not isinstance(race_info, dict):
        raise ValueError("shared-current-race-metadata-required")
    frame = horses.copy() if isinstance(horses, pd.DataFrame) else pd.DataFrame(horses)
    if frame.empty or frame.columns.duplicated().any():
        raise ValueError("shared-complete-current-card-required")
    rid = str(race_info.get("race_id") or "")
    meta, reason = canonical._race_metadata(rid, race_info)
    if reason:
        raise ValueError(f"shared-current-metadata-{reason}")
    rows = []
    for raw in frame.to_dict("records"):
        card = _weight_alias({name: value for name, value in raw.items() if name in _CARD_FIELDS})
        row, reason = canonical._runner(rid, card, meta)
        if reason:
            raise ValueError(f"shared-current-runner-{reason}")
        rows.append(row)
    current = pd.DataFrame(rows, columns=canonical.SOURCE_COLUMNS, index=frame.index)
    feature_state._cohort(current, historical=False)
    return current


def build_serving_features(db_path, race_info, horses):
    """Same raw47 formula as training; capture/time approval belongs upstream."""
    current = _current_card(race_info, horses)
    audit = _audit()
    day = str(current.race_date.iloc[0])
    with _database(db_path) as (connection, tables):
        history, selected = _load_history(connection, tables, current, day, audit, strict=True)
    features = build_training_features(current, history)
    no_history = features.safe_prior_starts.eq(0)
    audit["metadata_scope_exclusions"] = dict(audit["metadata_scope_exclusions"])
    diagnostics = {
        "schema": "prediction-input-contract-v1", "scope": FEATURE_VERSION,
        "feature_version": FEATURE_VERSION, "status": "unverified",
        "issue_codes": ["card_capture_provenance_unverified"],
        "semantic_parity_verified": True, "source_hashes": source_hashes(),
        "source_hash_matches": None, "live_capture_hash_bound": False,
        "card_capture_time_verified": False, "current_outcomes_consumed": False,
        "expected_count": len(FEATURE_COLUMNS), "actual_count": len(features.columns),
        "missing_columns": [], "restored_columns": [], "blocked_columns": [],
        "all_missing_columns": [name for name in features if features[name].isna().all()],
        "missing_value_rate": float(features.isna().to_numpy().mean()),
        "uncomputed_columns": [], "no_history_rows": int(no_history.sum()),
        "missing_horse_id_rows": int(current.horse_id.eq("").sum()),
        "same_day_or_future_history_used": False,
        "history_max_source_date": str(history.race_date.max()) if not history.empty else None,
        "history_rows": len(history), "history_audit": {**audit, **selected},
        "coverage_semantics": "observed eligible history, not complete career coverage",
        "calibration_verified": False, "profitability_verified": False,
    }
    return features, diagnostics
