from __future__ import annotations

from copy import deepcopy
import json
import numpy as np
import pandas as pd
import pytest

from market_quality.observations import (audit_odds, audit_calibration_split, audit_missingness,
                                         classify_odds_evidence)


def frame():
    records = []
    for date, phase in [("20210105", "calibration_seed"), ("20210711", "meta_fit"),
                        ("20211010", "development"), ("20220109", "validation")]:
        for number in (1, 2):
            records.append({"race_id": date + "0001", "race_date": date, "horse_number": number,
                            "expected_runner_count": 2, "surface": "turf", "phase": phase,
                            "fold_train_date_to": "20201231" if date.startswith("2021") else "20211231",
                            "finish": number, "h04_score": float(2 - number), "odds": float(number * 2),
                            "implied_probability": 1 / (number * 2),
                            "normalized_market_probability": (2 / 3 if number == 1 else 1 / 3),
                            "popularity": number})
    return pd.DataFrame(records)


def evidence(**changes):
    return {"evidence_ref": "fixture:immutable-http-receipt", "evidence_verified": True,
            "odds_binding_verified": True, "capture_kind": "direct_fetch", "http_status": 200,
            "source_sha256": "a" * 64, "source_url": "https://race.netkeiba.com/race/shutuba.html?race_id=202101050001",
            "source_time": "2021-01-05T14:54:00+09:00", "observed_at": "2021-01-05T14:54:01+09:00",
            "race_start_at": "2021-01-05T15:00:00+09:00", **changes}


def calibration_fixture():
    from prospective.calibration import _normalize, _hash_frame, _FIT_COLUMNS, transform_probabilities, SCHEMA
    data = frame()
    data["prediction"] = data.h04_score
    seed = data.loc[data.phase.eq("calibration_seed")]
    fitted = {"schema": SCHEMA, "family": "global_temperature", "market_data_used": False,
              "global": {"temperature": 1., "fit_runners": 2, "fit_races": 1}, "by_surface": {},
              "fit_years": [2021], "fit_date_from": "20210105", "fit_date_to": "20210105",
              "input_sha256": _hash_frame(_normalize(seed, require_oof=True), _FIT_COLUMNS)}
    scored = transform_probabilities(data, fitted)
    data["h04_probability"] = scored.win_probability
    data.loc[data.phase.eq("calibration_seed"), "h04_probability"] = np.nan
    data["h04_probability_fixed_reference"] = .5
    data = data.drop(columns="prediction")
    provenance = {"evaluation_only_columns": ["h04_probability_fixed_reference"],
                  "combined_features": ["h04_probability"], "market_features": ["normalized_market_probability"]}
    return data, fitted, provenance, {"fixture-model": ["h04_score", "h04_probability", "normalized_market_probability"]}


def test_a_requires_bound_direct_before_race_timestamps():
    assert classify_odds_evidence("20210105", evidence())[0] == "A"


def test_b_means_bound_pre_race_receipt_but_no_provider_tick():
    assert classify_odds_evidence("20210105", evidence(source_time=None))[0] == "B"


def test_2026_historical_reacquisition_never_becomes_t5():
    old = evidence(observed_at="2026-09-20T01:00:00+09:00", historical_source_verified=True,
                   source_kind="historical_result_sqlite", evidence_ref="fixture:snapshot-hash")
    quality, reason = classify_odds_evidence("20210105", old)
    assert quality == "C" and "not_contemporaneous" in reason
    old["historical_source_verified"] = False
    assert classify_odds_evidence("20210105", old)[0] == "D"


@pytest.mark.parametrize("mutation", [{"evidence_verified": False}, {"odds_binding_verified": False},
    {"source_sha256": "invalid"}, {"race_start_at": "2021-01-06T15:00:00+09:00"},
    {"source_time": "2021-01-05T14:54:02+09:00"}, {"source_time": "2021-01-05T14:54:00"},
    {"observed_at": "2021-01-05T15:00:00+09:00"}, {"capture_kind": "database_reload"}])
def test_unproven_or_inconsistent_times_do_not_get_ab(mutation):
    assert classify_odds_evidence("20210105", evidence(**mutation))[0] == "D"


def test_historical_common_provenance_and_probability_recomputation():
    result, summary = audit_odds(frame(), {"historical_source_verified": True,
        "source_kind": "historical_research_proxy", "evidence_ref": "fixture:audited-provenance-sha"})
    assert result.odds_quality.eq("C").all()
    assert summary["normalized_max_absolute_difference"] == 0
    assert summary["normalized_mismatched_races"] == 0
    assert summary["classification_complete"] is True
    assert summary["historical_odds_are_T5"] is False
    json.dumps(summary, allow_nan=False)


def test_unknown_evidence_does_not_guess_historical_endpoint():
    _, summary = audit_odds(frame())
    assert summary["classification"]["D"]["runners"] == 8
    assert not summary["classification_complete"]


def test_probability_mutation_detects_race_mismatch():
    data = frame()
    data.loc[0, "normalized_market_probability"] += .01
    _, summary = audit_odds(data)
    assert summary["normalized_mismatched_races"] == 1
    assert summary["normalized_mismatched_runners"] == 1
    assert summary["normalized_max_absolute_difference"] == pytest.approx(.01)


@pytest.mark.parametrize("bad", [0., -1., np.nan, np.inf, .5])
def test_bad_odds_prevent_whole_race_normalization(bad):
    data = frame()
    data.loc[0, "odds"] = bad
    result, summary = audit_odds(data)
    assert summary["invalid_odds"] == 1
    assert summary["normalization_unavailable_races"] == 1
    assert result.iloc[:2].market_probability_recomputed.isna().all()


def test_missing_runner_does_not_redefine_market_denominator():
    result, summary = audit_odds(frame().iloc[1:])
    assert summary["normalization_unavailable_races"] == 1
    assert result.iloc[0].normalization_unavailable


def test_calibration_hash_and_transform_replay_without_fit(monkeypatch):
    import prospective.calibration as module
    monkeypatch.setattr(module, "fit_calibrator", lambda *_a, **_k: pytest.fail("must not refit"))
    result = audit_calibration_split(*calibration_fixture())
    assert result["passed"], result
    assert result["later_probability_max_absolute_difference"] == 0
    assert result["calibrator_fit_performed"] is False


@pytest.mark.parametrize("mutation", ["fit_period", "fit_count", "input_hash", "reference_feature", "phase", "seed_probability", "probability"])
def test_calibration_mutations_fail_gate(mutation):
    data, fitted, provenance, columns = calibration_fixture()
    if mutation == "fit_period":
        fitted["fit_date_to"] = "20211231"
    elif mutation == "fit_count":
        fitted["global"]["fit_runners"] = 4
    elif mutation == "input_hash":
        fitted["input_sha256"] = "b" * 64
    elif mutation == "reference_feature":
        columns["fixture-model"].append("h04_probability_fixed_reference")
    elif mutation == "phase":
        data.loc[2, "phase"] = "development"
    elif mutation == "seed_probability":
        data.loc[0, "h04_probability"] = .5
    else:
        data.loc[3, "h04_probability"] += .01
    report = audit_calibration_split(data, fitted, provenance, columns)
    assert not report["passed"] and report["issues"]


def test_missingness_reasons_only_where_proven():
    data, _, _, _ = calibration_fixture()
    data["speed_figures"] = np.nan
    data["horse_weight_change"] = [0, -1, np.nan, -999, 2, 3, 4, 5]
    specs = {"speed_figures": {"missing_reason": "historical_unavailable", "reason_evidence": "fixture:2026-only-schema-audit"},
             "horse_weight_change": {"sentinels": [-999]}}
    report = audit_missingness(data, ["h04_probability", "speed_figures", "horse_weight_change", "training_data"], specs).set_index("feature")
    assert report.loc["h04_probability", "missing_count"] == 2
    assert json.loads(report.loc["h04_probability", "missing_or_invalid_reason_counts"])["legitimate_missing"] == 2
    assert json.loads(report.loc["speed_figures", "missing_or_invalid_reason_counts"])["historical_unavailable"] == 8
    assert report.loc["horse_weight_change", "zero_count"] == 1
    assert report.loc["horse_weight_change", "invalid_count"] == 1
    assert report.loc["horse_weight_change", "sentinel_count"] == 1
    assert json.loads(report.loc["training_data", "missing_or_invalid_reason_counts"])["reason_unknown"] == 8


def test_declaring_parser_failure_without_evidence_is_unknown():
    result = audit_missingness(frame(), ["absent"], {"absent": {"missing_reason": "parser_failure"}})
    assert json.loads(result.iloc[0].missing_or_invalid_reason_counts) == {"reason_unknown": 8}


@pytest.mark.parametrize("kind", ["future", "duplicate"])
def test_later_results_or_duplicate_keys_are_not_audited(kind):
    data = frame()
    if kind == "future":
        data.loc[0, "race_date"] = "20230101"
    else:
        data.loc[1, "horse_number"] = 1
    with pytest.raises(ValueError):
        audit_odds(data)


def test_supplement_presence_is_not_historical_availability(tmp_path):
    import importlib.util
    import sqlite3
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "scripts/audit_market_missingness.py"
    spec = importlib.util.spec_from_file_location("audit_market_missingness_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    snapshot = tmp_path / "snapshot.db"
    db = sqlite3.connect(snapshot)
    for table in ("training_data", "speed_figures"):
        db.execute("CREATE TABLE " + table + "(id INTEGER PRIMARY KEY,race_id TEXT,horse_number INTEGER,created_at TEXT,secret_value TEXT)")
        db.execute("INSERT INTO " + table + " VALUES(1,'202101050001',1,'2026-05-31 03:00:00','must-not-load')")
        db.execute("INSERT INTO " + table + " VALUES(2,'202601010001',1,'2026-05-31 03:00:00','future-must-not-load')")
    db.commit()
    db.close()
    before = snapshot.read_bytes()
    evidence, presence = module.supplemental_metadata(snapshot, frame())
    assert snapshot.read_bytes() == before
    for table in ("training_data", "speed_figures"):
        report = evidence[table]
        assert report["target_race_records"] == 1
        assert report["target_runners_with_records"] == 1
        assert not report["asof_usable"] and not report["values_loaded_or_used"]
        assert all("secret_value" not in record for record in report["metadata_rows"])
        assert presence[table + "_stored_record_count"].sum() == 1


def test_categorical_features_not_misclassified_as_numeric_missingness():
    data = frame()
    columns = ["safe_surface", "safe_venue", "safe_sex", "safe_field_condition", "safe_weather", "safe_race_class"]
    for column in columns:
        data[column] = "known-category"
    report = audit_missingness(data, columns, {column: {"kind": "categorical"} for column in columns})
    assert report.invalid_count.eq(0).all()
    assert report.missing_count.eq(0).all()
