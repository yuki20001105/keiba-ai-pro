from datetime import timedelta
import sqlite3

import pytest

from test_betting_performance import NOW, result_db, snapshot

SHA = "b" * 64


def model():
    return {
        "_shared_artifact_sha256": SHA, "feature_pipeline": "local-db-shared-optimized-v1",
        "feature_columns": ["a", "b", "c"], "feature_source_hashes": {"features": "a" * 64},
        "training_date_to": "20220604", "dataset_period_end": "20260927",
        "probability_validation": {"calibration_period_end": "2024-08-10", "validation_period_end": "2026-09-27"},
    }


def prediction(rid="race-one", day="20261010", model_id="model-A"):
    value = snapshot(rid=rid, day=day, skip=True)
    value["success"] = True
    value["model_id"] = model_id
    value["race_info"]["num_horses"] = 5
    value["predictions"] = [
        {"horse_number": i, "p_norm": p, "odds": odds}
        for i, (p, odds) in enumerate(zip([.4, .25, .15, .12, .08], [2, 5, 8, 12, 20]), 1)
    ]
    value["input_diagnostics"].update(
        status="unverified", issue_codes=["pre_race_db_capture_unverified"],
        current_outcomes_consumed=False, missing_value_rate=.1,
        feature_pipeline="local-db-shared-optimized-v1", feature_source_hashes={"features": "a" * 64},
    )
    return value


def record(db, value=None, *, owner="alice", bundle=None, now=NOW, **kwargs):
    from betting.paper_simulation import record_paper_prediction
    return record_paper_prediction(db, owner, prediction() if value is None else value,
                                   model() if bundle is None else bundle, model_sha256=SHA, now=now, **kwargs)


def overview(db, results, owner="alice", **kwargs):
    from betting.paper_simulation import list_paper_performance
    return list_paper_performance(db, results, owner, **kwargs)


def monthly(db, results, year=2026, owner="alice", **kwargs):
    from betting.paper_simulation import monthly_paper_performance
    return monthly_paper_performance(db, results, owner, year, **kwargs)


def official(results, rid="race-one", day="20261010", hit=True):
    result_db(results, rid=rid, finishes=(2, 3, 4, 1, 5) if hit else (4, 5, 2, 3, 1),
              info={"date": day, "num_horses": 5},
              dividends=[("ワイド", "1-2", 800), ("ワイド", "1-4", 200), ("ワイド", "2-4", 300),
                         ("三連単", "4-1-2", 5000)] if hit else
                        [("ワイド", "3-4", 100), ("ワイド", "3-5", 200), ("ワイド", "4-5", 300),
                         ("三連単", "5-3-4", 1000)])


def test_exact_test_membership_admits_old_test_not_training_or_calibration(tmp_path):
    db, results = tmp_path / "ledger.db", tmp_path / "results.db"
    bundle = model()
    bundle.update(training_date_to="20261017", dataset_period_end="20261017",
                  _monthly_reference={"partition_sha256": "c" * 64,
                                      "test_dates": {"heldout": "2020-10-10"},
                                      "development_races": frozenset({"train", "cal"})})
    heldout = prediction("heldout", "20201010")
    first = record(db, heldout, bundle=bundle, historical_validation=True)
    assert first["eligible"] is True
    assert first["purchase_eligible"] is False
    assert record(db, heldout, bundle=bundle, historical_validation=True)["existing"] is True
    for rid in ("train", "cal", "unassigned"):
        assert record(db, prediction(rid, "20201010"), bundle=bundle,
                      historical_validation=True)["eligible"] is False
    assert record(db, prediction("heldout", "20201011"), bundle=bundle,
                  historical_validation=True)["eligible"] is False
    official(results, "heldout", "20201010")
    data = overview(db, results, origin="historical_validation")
    assert data["summary"]["settled_races"] == 1
    assert data["model_performance"]["models"][0]["training_end_date"] == "2026-10-17"
    assert monthly(db, results, year=2020, origin="historical_validation")["models"][0]["months"][9]["race_count"] == 1
    assert overview(db, results, origin="all")["total_count"] == 0
    assert overview(db, results, owner="bob", origin="historical_validation")["total_count"] == 0
    future = record(db, prediction("new-unseen", "20271010"), bundle=bundle)
    assert future["eligible"] is True


def test_combined_scope_includes_saved_test_and_live_predictions_once_with_breakdown(tmp_path):
    db, results = tmp_path / "ledger.db", tmp_path / "results.db"
    bundle = model()
    bundle.update(
        training_date_to="20261017", dataset_period_end="20261017",
        _monthly_reference={"partition_sha256": "c" * 64,
                            "test_dates": {"heldout": "2020-10-10"},
                            "development_races": frozenset({"train", "cal"})},
    )
    saved = record(db, prediction("heldout", "20201010"), bundle=bundle, historical_validation=True)
    assert saved["eligible"]
    official(results, "heldout", "20201010")
    live = prediction("live", "20271010")
    assert record(db, live, bundle=bundle)["origin"] == "pre_race"
    assert record(db, live, bundle=bundle)["existing"]
    official(results, "live", "20271010", hit=False)
    assert record(db, prediction("waiting", "20271011"), bundle=bundle)["eligible"]
    assert not record(db, prediction("train", "20201010"), bundle=bundle, historical_validation=True)["eligible"]
    data = overview(db, results, origin="combined")
    model_data = data["model_performance"]["models"][0]
    assert data["total_count"] == 3
    assert model_data["race_count"] == 2
    assert model_data["total_cost"] == 3000
    assert model_data["total_return"] == 29000
    assert model_data["cohorts"]["evaluation"]["race_count"] == 1
    assert model_data["cohorts"]["pre_race"]["race_count"] == 1
    assert model_data["status_counts"]["pending"] == 1
    assert model_data["cohorts"]["pre_race"]["pending_races"] == 1
    assert len(model_data["points"]) == 2
    assert overview(db, results, owner="bob", origin="combined")["total_count"] == 0
    assert monthly(db, results, year=2020, origin="combined")["models"][0]["months"][9]["total_cost"] == 1500
    filtered = overview(db, results, origin="combined", date_from="20270101")
    assert filtered["model_performance"]["models"][0]["total_return"] == 0
    assert overview(db, results, origin="all")["total_count"] == 2
    assert overview(db, results, origin="historical_validation")["total_count"] == 1


@pytest.mark.parametrize("mutate", [
    lambda p, b: p["race_info"].update(date="20260927"),
    lambda p, b: b.update(dataset_period_end=None),
    lambda p, b: b["probability_validation"].update(validation_period_end="2026-10-10"),
    lambda p, b: p["input_diagnostics"].update(current_outcomes_consumed=True),
    lambda p, b: p["input_diagnostics"].update(semantic_parity_verified=False),
    lambda p, b: p["input_diagnostics"].update(feature_source_hashes={"features": "changed"}),
])
def test_simulation_excludes_seen_or_unverified_predictions(tmp_path, mutate):
    value, bundle = prediction(), model()
    mutate(value, bundle)
    assert record(tmp_path / "ledger.db", value, bundle=bundle)["eligible"] is False
    assert overview(tmp_path / "ledger.db", tmp_path / "absent.db")["model_performance"]["models"] == []


def test_reference_book_is_frozen_per_owner_model_and_never_grants_purchase(tmp_path):
    db, results = tmp_path / "ledger.db", tmp_path / "results.db"
    first = record(db)
    changed = prediction()
    changed["predictions"][1]["odds"] = 30
    assert record(db, changed)["record_id"] == first["record_id"]
    assert record(db, prediction(model_id="model-B"))["record_id"] != first["record_id"]
    assert record(db, owner="bob")["record_id"] != first["record_id"]
    official(results)
    data = overview(db, results)["model_performance"]
    assert data["source"] == "unseen-simulation" and data["point_granularity"] == "day"
    a, b = data["models"]
    assert a["total_cost"] == b["total_cost"] == 1500
    assert a["total_return"] == b["total_return"] == 29000
    assert a["cutoff_date"] == "2026-09-27" and a["color"] != b["color"]
    assert first["purchase_eligible"] is False and first["origin"] == "pre_race"
    assert overview(db, results, owner="charlie")["model_performance"]["models"] == []
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM betting_performance_records").fetchone()[0] == 0


def test_replay_summary_reconciles_only_selected_model_and_manifest_races(tmp_path):
    db, results = tmp_path / "ledger.db", tmp_path / "results.db"
    record(db)
    record(db, prediction(model_id="model-B"))
    record(db, prediction(rid="outside-manifest"))
    official(results)
    official(results, rid="outside-manifest")
    scoped = overview(db, results, model_id="model-A", race_ids={"race-one"})
    assert scoped["total_count"] == 1
    assert scoped["summary"]["settled_cost"] == 1500
    assert scoped["summary"]["settled_return"] == 29000
    assert [m["model_id"] for m in scoped["model_performance"]["models"]] == ["model-A"]
    assert overview(db, results, model_id="model-A", race_ids=set())["total_count"] == 0
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM paper_simulation_records WHERE status='settled'").fetchone()[0] == 1


def test_monthly_query_resets_month_totals_and_keeps_no_data_null(tmp_path):
    db, results = tmp_path / "ledger.db", tmp_path / "results.db"
    for rid, day, hit in [("oct-hit", "20261010", True), ("oct-miss", "20261011", False),
                          ("nov-hit", "20261110", True), ("next-year", "20270110", True)]:
        record(db, prediction(rid, day), now=NOW + timedelta(days=100))
        official(results, rid, day, hit)
    record(db, prediction("pending", "20261111"))
    value = monthly(db, results)["models"][0]
    assert len(value["months"]) == 12
    assert value["months"][0]["roi"] is None
    assert value["months"][9]["total_cost"] == 3000
    assert value["months"][9]["total_return"] == 29000
    assert value["months"][9]["roi"] == 966.7
    assert value["months"][10]["race_count"] == 1
    assert monthly(db, results, year=2027)["models"][0]["months"][0]["total_cost"] == 1500
    filtered = overview(db, results, date_from="20261101", date_to="20261130")
    assert filtered["model_performance"]["models"][0]["total_cost"] == 1500
    assert filtered["summary"]["pending_races"] == 1
    assert overview(db, results, origin="pre_race")["model_performance"]["models"][0]["race_count"] == 0


def test_missing_payout_is_not_a_zero_return_and_colors_survive_filtering(tmp_path):
    db, results = tmp_path / "ledger.db", tmp_path / "results.db"
    record(db)
    record(db, prediction("second", model_id="model-B"))
    official(results)
    with sqlite3.connect(results) as con:
        con.execute("DELETE FROM return_tables_ultimate WHERE bet_type='三連単'")
    data = overview(db, results)
    assert data["summary"]["settled_races"] == 0
    assert data["summary"]["pending_races"] == 2
    assert all(value["roi"] is None for value in data["model_performance"]["models"])
    before = {m["model_id"]: m["color"] for m in data["model_performance"]["models"]}
    official(results)
    after = {m["model_id"]: m["color"] for m in monthly(db, results)["models"]}
    assert before == after


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(success=False),
    lambda p: p["race_info"].update(post_time=None),
    lambda p: p["race_info"].update(num_horses=6),
    lambda p: p["predictions"][0].update(odds=0),
])
def test_invalid_predictions_never_become_historical_simulations(tmp_path, mutate):
    value = prediction(day="20990101")
    mutate(value)
    assert record(tmp_path / "ledger.db", value)["eligible"] is False


def test_same_model_id_cannot_silently_change_artifact(tmp_path):
    from betting.paper_simulation import record_paper_prediction
    db = tmp_path / "ledger.db"
    first = record(db)
    changed = model()
    changed["_shared_artifact_sha256"] = "c" * 64
    value = record_paper_prediction(db, "alice", prediction(), changed, model_sha256="c" * 64, now=NOW)
    assert not value["eligible"] and value["reason"] == "registered_model_identity_changed"
    assert record(db)["record_id"] == first["record_id"]


def test_native_missing_history_does_not_apply_purchase_gates_to_reference_cohort(tmp_path):
    value = prediction()
    value["input_diagnostics"].update(missing_value_rate=.59, no_history_rows=5)
    assert record(tmp_path / "ledger.db", value)["eligible"] is True


@pytest.mark.parametrize("day,eligible", [("20220603", False), ("20220604", False), ("20220605", True)])
def test_retrospective_replay_never_admits_a_training_race(tmp_path, day, eligible):
    value = prediction(day=day)
    value["input_diagnostics"]["issue_codes"].append("target_not_after_training_evaluation_period")
    saved = record(tmp_path / "ledger.db", value, historical_validation=True)
    assert saved["eligible"] is eligible
    assert saved["purchase_eligible"] is False


def test_retrospective_scope_includes_calibration_and_test_without_changing_unseen_totals(tmp_path):
    db, results = tmp_path / "ledger.db", tmp_path / "results.db"
    for day in ("20220605", "20230520", "20240811", "20260927", "20261009"):
        value = prediction(rid=day, day=day)
        if day <= "20260927":
            value["input_diagnostics"]["issue_codes"].append("target_not_after_training_evaluation_period")
            assert record(db, value)["eligible"] is False
        assert record(db, value, historical_validation=True)["eligible"]
        official(results, day, day)
    strict = overview(db, results)
    assert strict["total_count"] == strict["summary"]["settled_races"] == 1
    assert strict["model_performance"]["models"][0]["total_cost"] == 1500
    retrospective = overview(db, results, origin="historical_validation")
    assert retrospective["total_count"] == retrospective["summary"]["settled_races"] == 5
    assert retrospective["model_performance"]["source"] == "historical-validation-simulation"
    assert retrospective["model_performance"]["evaluation_scope"] == "non-training-retrospective"
    assert retrospective["model_performance"]["models"][0]["training_end_date"] == "2022-06-04"
    assert retrospective["model_performance"]["years"] == ["2022", "2023", "2024", "2026"]
    assert overview(db, results, owner="bob", origin="historical_validation")["total_count"] == 0
    assert monthly(db, results, year=2024, origin="historical_validation")["models"][0]["months"][7]["total_cost"] == 1500
    assert monthly(db, results, year=2024)["models"][0]["months"][7]["roi"] is None


@pytest.mark.parametrize("day,eligible", [("20261009", True), ("20261010", False), ("20990101", False)])
def test_date_alone_proves_retrospective_timing_only_before_today(tmp_path, day, eligible):
    value = prediction(day=day)
    value["race_info"]["post_time"] = None
    saved = record(tmp_path / "ledger.db", value, historical_validation=True)
    assert saved["eligible"] is eligible
    if eligible:
        assert saved["origin"] == "historical"


@pytest.mark.parametrize("change", ["current_outcomes", "unknown_issue", "artifact", "future_capture"])
def test_retrospective_scope_does_not_relax_prediction_provenance(tmp_path, change):
    value, bundle, now = prediction(day="20240811"), model(), NOW
    value["input_diagnostics"]["issue_codes"].append("target_not_after_training_evaluation_period")
    if change == "current_outcomes":
        value["input_diagnostics"]["current_outcomes_consumed"] = True
    elif change == "unknown_issue":
        value["input_diagnostics"]["issue_codes"].append("unverified_feature_source")
    elif change == "artifact":
        bundle["_shared_artifact_sha256"] = "c" * 64
    else:
        now -= timedelta(days=790)
    assert record(tmp_path / "ledger.db", value, bundle=bundle, now=now, historical_validation=True)["eligible"] is False


def test_legacy_registry_gets_training_boundary_only_from_the_same_verified_artifact(tmp_path):
    db = tmp_path / "ledger.db"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE paper_simulation_models (owner TEXT, model_id TEXT, model_sha256 TEXT, "
                    "cutoff_date TEXT, display_name TEXT, color TEXT, PRIMARY KEY(owner,model_id))")
        con.execute("INSERT INTO paper_simulation_models VALUES (?,?,?,?,?,?)",
                    ("alice", "model-A", SHA, "2026-09-27", "Original name", "#a78bfa"))
    assert record(db, prediction(day="20240811"), historical_validation=True)["eligible"]
    bundle = model()
    bundle["training_date_to"] = "20220605"
    rejected = record(db, prediction("changed", "20240812"), bundle=bundle, historical_validation=True)
    assert rejected["reason"] == "registered_model_identity_changed"
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT training_end_date,display_name,color FROM paper_simulation_models").fetchone() == (
            "2022-06-04", "Original name", "#a78bfa")
