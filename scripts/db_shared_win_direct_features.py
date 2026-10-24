"""Shared 88 point-in-time features for isolated direct-win research/capture.

Historical construction reuses the frozen day-batched equations and verifies
the original 46 columns exactly against the pinned raw47 matrix. Current-card
construction reuses the frozen previous-day state, never fabricated current
results. The caller must provide all quality-selected JRA-flat history since
2013, not a horse-only subset, for identical rider/trainer aggregates. This
module cannot fetch data, publish models or grant pre-start/EV authority.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
for _directory in (ROOT / "python-api", ROOT / "keiba", ROOT / "scripts"):
    if str(_directory) not in sys.path:
        sys.path.insert(0, str(_directory))

from experiments import dataset as canonical
from experiments import features as frozen
from experiments import search_features as search
from forward_adapters import feature_state
import db_shared_win_direct_worker as worker

SCHEMA = "research-db-shared-win-direct-features-v1"
IDENTITY_COLUMNS = ("race_id", "race_date", "horse_number", "horse_id")
RAW47_COLUMNS = tuple(frozen.FEATURE_GROUPS["base"] + frozen.HISTORY_COLUMNS)
BASE46_COLUMNS = tuple(name for name in RAW47_COLUMNS if name != "safe_past5_top3_rate")
FEATURE_COLUMNS = tuple(worker.feature_contract("market46_extended")[0][4:])
CATEGORICAL_COLUMNS = tuple(frozen.BASE_CATEGORICAL)
MARKET_COLUMNS = tuple(worker.MARKET_COLUMNS)
_CURRENT_EXCLUDED = frozenset({"finish", "time_seconds", "last_3f_time", "odds", "popularity"})
_CARD_FIELDS = tuple(name for name in canonical.SOURCE_COLUMNS if name not in _CURRENT_EXCLUDED)
HISTORY_CONTRACT = {
    "scope": "all observed complete quality-selected JRA flat races, all runners, from 2013-01-01",
    "history_floor": "20130101", "predicate": "source_race_date < target_race_date",
    "same_day_results_allowed": False, "current_horse_only_history_allowed": False,
    "entity_context": "jockey/trainer support and relative jockey forms require the complete supplied race universe",
    "coverage_semantics": "observed eligible history, not verified complete careers or every official race",
    "full_world_completeness_verified": False,
}


def _source_hashes():
    modules = (canonical, frozen, search, feature_state, worker)
    return {Path(module.__file__).resolve().relative_to(ROOT).as_posix(): worker.file_hash(module.__file__) for module in modules} | {
        Path(__file__).resolve().relative_to(ROOT).as_posix(): worker.file_hash(__file__)}


_IMPORTED_SOURCES = _source_hashes()


def source_hashes():
    actual = _source_hashes()
    if actual != _IMPORTED_SOURCES:
        raise ValueError("win-direct-feature-source-changed-since-import")
    return dict(actual)


def _ordered_history(frame):
    if not isinstance(frame, pd.DataFrame) or frame.empty or frame.columns.duplicated().any():
        raise ValueError("win-direct-feature-complete-history-required")
    checked = feature_state._cohort(frame, historical=True)
    if checked.race_date.lt("20130101").any():
        raise ValueError("win-direct-feature-history-floor-2013-required")
    return checked


def _raw47_parity(expected, raw47, identities, *, input_identities=None):
    """Exact values/NaN/categories; positional cache alignment remains explicit."""
    if not isinstance(raw47, pd.DataFrame) or raw47.columns.duplicated().any() or len(raw47) != len(expected):
        raise ValueError("win-direct-verified-raw47-frame-required")
    if not set(RAW47_COLUMNS) <= set(raw47):
        raise ValueError("win-direct-raw47-contract-columns-missing")
    if input_identities is None:
        input_identities = identities
    # A raw cache may contain only the 47 values; its verified target companion
    # supplies the positional identity map. An identity-bearing matrix must
    # also prove matching keys rather than trusting its row order.
    keys = ["race_id", "race_date", "horse_number"]
    actual = raw47.reset_index(drop=True).copy()
    source_keys = input_identities.reset_index(drop=True).loc[:, keys]
    if set(keys) <= set(raw47):
        observed_keys = raw47.reset_index(drop=True).loc[:, keys].copy()
        if not observed_keys.astype(str).equals(source_keys.astype(str)):
            raise ValueError("win-direct-raw47-input-key-alignment-mismatch")
    actual[keys] = source_keys.to_numpy()
    actual = actual.set_index(keys)
    wanted = pd.MultiIndex.from_frame(identities.loc[:, keys])
    if actual.index.has_duplicates or set(actual.index) != set(wanted):
        raise ValueError("win-direct-raw47-key-cohort-mismatch")
    actual = actual.loc[wanted, list(BASE46_COLUMNS)].reset_index(drop=True)
    expected = expected.reset_index(drop=True).loc[:, list(BASE46_COLUMNS)]
    for name in BASE46_COLUMNS:
        if name in CATEGORICAL_COLUMNS:
            left = expected[name].astype("string")
            right = actual[name].astype("string")
            equal = left.eq(right) | (left.isna() & right.isna())
            if not equal.fillna(False).all():
                raise ValueError("win-direct-base46-category-parity-mismatch:" + name)
        else:
            left, right = (pd.to_numeric(value[name], errors="coerce").to_numpy(float) for value in (expected, actual))
            if np.isinf(left).any() or np.isinf(right).any() or not np.array_equal(left, right, equal_nan=True):
                raise ValueError("win-direct-base46-numeric-parity-mismatch:" + name)
    return {"verified": True, "columns": list(BASE46_COLUMNS), "feature_count": 46,
            "exact_numeric_and_nan_equality": True, "exact_categorical_equality": True,
            "identity_sha256": worker.frame_hash(identities.loc[:, list(IDENTITY_COLUMNS)]),
            "raw47_input_sha256": worker.frame_hash(raw47.loc[:, list(RAW47_COLUMNS)]),
            "alignment": "race/date/horse_number keys supplied by the verified raw47 target companion",
            "raw47_has_explicit_keys": set(keys) <= set(raw47)}


def _finish_result(generated):
    if len(FEATURE_COLUMNS) != 88 or len(set(FEATURE_COLUMNS)) != 88 or not set(FEATURE_COLUMNS) <= set(generated):
        raise ValueError("win-direct-fixed-88-feature-contract-invalid")
    result = generated.loc[:, list(IDENTITY_COLUMNS) + list(FEATURE_COLUMNS)].copy()
    if result.duplicated(["race_id", "horse_number"]).any():
        raise ValueError("win-direct-feature-runner-keys-duplicated")
    return result


def build_training_features(target, raw47):
    """One frozen historical pass, mandatory exact base46/cache parity."""
    sources = source_hashes()
    input_hash = worker.frame_hash(target)
    source = _ordered_history(target)
    generated, audit = frozen.engineer_features(source)
    derived = search.derive_search_features(generated)
    full = pd.concat([generated, derived], axis=1)
    result = _finish_result(full)
    parity = _raw47_parity(result, raw47, result.loc[:, list(IDENTITY_COLUMNS)], input_identities=target)
    point_in_time = audit["point_in_time_audit"]
    if (point_in_time["strict_predicate"] != "source_race_date < target_race_date"
            or point_in_time["same_day_future_references"] != 0 or point_in_time["future_leakage_rows"] != 0
            or point_in_time["same_day_results_excluded"] is not True):
        raise ValueError("win-direct-feature-point-in-time-contract-failed")
    receipt = {"schema": SCHEMA, "mode": "historical", "source_hashes": sources,
        "feature_columns": list(FEATURE_COLUMNS), "feature_count": 88, "identity_columns": list(IDENTITY_COLUMNS),
        "history_contract": dict(HISTORY_CONTRACT), "point_in_time_audit": point_in_time,
        "rows": len(result), "races": int(result.race_id.nunique()),
        "date_from": result.race_date.min(), "date_to": result.race_date.max(),
        "input_sha256": input_hash, "frame_sha256": worker.frame_hash(result),
        "formula_sha256": worker.canonical_hash({"sources": sources, "columns": list(FEATURE_COLUMNS)}),
        "base46_parity": parity, "target_date_outcomes_used_for_own_features": False,
        "input_outcomes_consumed": False, "historical_outcomes_used_for_later_dates": True,
        "same_day_or_future_history_used": False, "dummy_current_results_used": False,
        "prestart_capture_provenance_verified": False, "complete_career_coverage_verified": False,
        "eligible_for_ev": False, "artifact_published": False}
    if source_hashes() != sources or worker.frame_hash(target) != input_hash:
        raise ValueError("win-direct-historical-input-or-source-mutated")
    result.attrs.update(receipt=receipt)
    return result, receipt


def build_current_features(current, history, raw47=None):
    """No dummy targets; complete previous-day state reused for a whole card."""
    sources = source_hashes()
    if not isinstance(current, pd.DataFrame) or current.empty or current.columns.duplicated().any():
        raise ValueError("win-direct-current-complete-card-required")
    # This projection happens before any validation, conversion or frame hash
    # so even poison current outcome/price columns cannot enter feature state.
    card = current.reindex(columns=list(_CARD_FIELDS)).copy()
    checked = feature_state._cohort(card, historical=False)
    if checked.race_date.nunique() != 1:
        raise ValueError("win-direct-current-one-target-date-required")
    day = checked.race_date.iloc[0]
    if day < "20130101":
        raise ValueError("win-direct-current-date-before-history-contract")
    if not isinstance(history, pd.DataFrame) or history.columns.duplicated().any():
        raise ValueError("win-direct-previous-history-dataframe-required")
    # Frozen state fails closed on same-day/future rows, rather than dropping
    # them silently; upstream data loading must attest the same complete scope.
    envelope = feature_state.build_history_state(history, target_date=day)
    generated = feature_state.build_current_features(envelope, checked)
    result = _finish_result(generated)
    parity = None if raw47 is None else _raw47_parity(result, raw47,
        result.loc[:, list(IDENTITY_COLUMNS)], input_identities=current)
    state = envelope["state"]
    receipt = {"schema": SCHEMA, "mode": "current", "source_hashes": sources,
        "feature_columns": list(FEATURE_COLUMNS), "feature_count": 88, "identity_columns": list(IDENTITY_COLUMNS),
        "history_contract": dict(HISTORY_CONTRACT), "target_date": day,
        "state_sha256": envelope["state_sha256"], "history_source_rows_sha256": state["source_rows_sha256"],
        "history_rows": state["history_row_count"], "history_races": state["history_race_count"],
        "history_date_from": state["history_min_source_date"], "history_date_to": state["history_max_source_date"],
        "rows": len(result), "races": int(result.race_id.nunique()),
        "card_input_sha256": worker.frame_hash(checked.reindex(columns=list(_CARD_FIELDS))),
        "frame_sha256": worker.frame_hash(result), "base46_parity": parity,
        "formula_sha256": worker.canonical_hash({"sources": sources, "columns": list(FEATURE_COLUMNS)}),
        "input_outcomes_consumed": False, "same_day_or_future_history_used": False,
        "historical_outcomes_used_for_later_dates": True, "dummy_current_results_used": False,
        "card_fields_consumed": list(_CARD_FIELDS), "prestart_capture_provenance_verified": False,
        "complete_career_coverage_verified": False, "eligible_for_ev": False, "artifact_published": False}
    if source_hashes() != sources:
        raise ValueError("win-direct-current-feature-source-mutated")
    result.attrs.update(receipt=receipt)
    return result, receipt


def add_market_features(frame):
    """Copy+four quote features, no outcome/payout reads or row filtering."""
    required = {"race_id", "horse_number", "odds", "expected_runner_count"}
    if (not isinstance(frame, pd.DataFrame) or frame.empty or frame.columns.duplicated().any()
            or not required <= set(frame)):
        raise ValueError("win-direct-complete-quoted-field-required")
    # Read only the explicit quote/key columns. Existing outcomes pass through
    # unchanged in the returned copy but cannot affect these four computations.
    quotes = frame.loc[:, ["race_id", "horse_number", "odds", "expected_runner_count"]].copy()
    if not quotes.race_id.astype("string").str.fullmatch(r"\d{12}").fillna(False).all():
        raise ValueError("win-direct-market-race-keys-invalid")
    numbers = pd.to_numeric(quotes.horse_number, errors="coerce")
    odds = pd.to_numeric(quotes.odds, errors="coerce")
    counts = pd.to_numeric(quotes.expected_runner_count, errors="coerce")
    if (not np.isfinite(numbers).all() or not numbers.between(1, 18).all() or not numbers.eq(np.floor(numbers)).all()
            or not np.isfinite(odds).all() or not odds.ge(1.).all()
            or not np.isfinite(counts).all() or not counts.between(2, 18).all() or not counts.eq(np.floor(counts)).all()
            or quotes.duplicated(["race_id", "horse_number"]).any()):
        raise ValueError("win-direct-market-numeric-quotes-invalid")
    observed = quotes.groupby("race_id", sort=False).race_id.transform("size")
    if not counts.eq(observed).all():
        raise ValueError("win-direct-market-whole-runner-field-required")
    if "num_horses" in frame and not pd.to_numeric(frame.num_horses, errors="coerce").eq(observed).all():
        raise ValueError("win-direct-market-declared-count-conflict")
    inverse = 1. / odds
    market = inverse / inverse.groupby(quotes.race_id, sort=False).transform("sum")
    result = frame.copy()
    result["normalized_market_probability"] = market.to_numpy(float)
    result["log_odds"] = np.log(odds).to_numpy(float)
    result["market_rank"] = odds.groupby(quotes.race_id, sort=False).rank(method="min", ascending=True).to_numpy(float)
    result["field_size"] = observed.to_numpy(float)
    if not np.isfinite(result.loc[:, list(MARKET_COLUMNS)].to_numpy(float)).all():
        raise ValueError("win-direct-market-features-nonfinite")
    return result
