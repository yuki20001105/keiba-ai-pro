"""Limited isolated workflow contract tests; no live/source DB writes."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import socket
import sqlite3
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("local_workflow_proof", ROOT / "scripts" / "verify_local_workflow.py")
proof = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = proof
SPEC.loader.exec_module(proof)


def source_payload(rid="202101010101", *, finishes=(3, 1, 4, 2)):
    return {
        "race_info": {"race_id": rid, "date": "20210101", "num_horses": 4,
            "venue": "札幌", "distance": 1200, "track_type": "芝", "post_time": "12:00"},
        "horses": [{"horse_number": number, "horse_id": f"h{number}",
            "finish_position": finish, "finish_time": "1:10.0", "odds": 5., "popularity": number,
            "horse_name": f"horse-{number}"} for number, finish in enumerate(finishes, 1)],
        "return_tables": [{"bet_type": kind, "combinations": combination, "payout": payout, "popularity": None}
            for kind, combination, payout in [
                ("単勝", "2", 350), ("馬連", "2-4", 800), ("馬単", "2-4", 1600),
                ("ワイド", "2-4", 200), ("ワイド", "1-2", 300), ("ワイド", "1-4", 400),
                ("三連複", "1-2-4", 2500), ("三連単", "2-4-1", 4000),
            ]],
    }


def raw_source(path):
    with sqlite3.connect(path) as database:
        database.executescript("""
            CREATE TABLE races_ultimate(race_id TEXT PRIMARY KEY,data TEXT);
            CREATE TABLE race_results_ultimate(id INTEGER PRIMARY KEY,race_id TEXT,data TEXT);
            CREATE TABLE return_tables_ultimate(id INTEGER PRIMARY KEY,race_id TEXT,bet_type TEXT,combinations TEXT,payout INTEGER,popularity INTEGER);
        """)
        source = source_payload()
        database.execute("INSERT INTO races_ultimate VALUES (?,?)", (source["race_info"]["race_id"], json.dumps(source["race_info"])))
        database.executemany("INSERT INTO race_results_ultimate(race_id,data) VALUES (?,?)",
            [(source["race_info"]["race_id"], json.dumps(h)) for h in source["horses"]])
        database.executemany("INSERT INTO return_tables_ultimate(race_id,bet_type,combinations,payout,popularity) VALUES (?,?,?,?,?)",
            [(source["race_info"]["race_id"], d["bet_type"], d["combinations"], d["payout"], d["popularity"]) for d in source["return_tables"]])
    return path


def test_readonly_load_is_deterministic_and_reports_real_denominator(tmp_path):
    path = raw_source(tmp_path / "source.db")
    first, inventory = proof.load_source(path, 12)
    second, repeat = proof.load_source(path, 12)
    assert first == second == [source_payload()]
    assert inventory == repeat
    assert inventory["races"] == 1 and inventory["runner_rows"] == 4
    assert inventory["selected_races"] == 1 and inventory["dividend_rows"] == 8
    assert inventory["source_rows_sha256"] == proof.digest(first)


@pytest.mark.parametrize("limit", [0, 33, True, 1.5, "12"])
def test_source_replay_is_bounded(tmp_path, limit):
    with pytest.raises(ValueError):
        proof.load_source(tmp_path / "not-read.db", limit)


def test_guard_denies_source_writes_even_when_source_is_inside_workspace(tmp_path):
    source = raw_source(tmp_path / "source.db")
    with proof.isolated_io(tmp_path, source) as counters:
        with pytest.raises(PermissionError):
            sqlite3.connect(source)
        with proof.readonly(source) as database:
            with pytest.raises(sqlite3.OperationalError):
                database.execute("DELETE FROM races_ultimate")
        with sqlite3.connect(tmp_path / "scratch.db") as database:
            database.execute("CREATE TABLE scratch(value INTEGER)")
        assert counters["sqlite_denied"] == 1 and counters["source_ro_connections"] == 1


def test_guard_denies_sibling_database_and_network(tmp_path):
    source = raw_source(tmp_path / "source.db")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with proof.isolated_io(workspace, source) as counters:
        with pytest.raises(PermissionError):
            sqlite3.connect(tmp_path / "outside.db")
        with pytest.raises(PermissionError):
            socket.create_connection(("example.com", 443))
        with pytest.raises(PermissionError):
            socket.getaddrinfo("example.com", 443)
        assert counters["sqlite_denied"] == 1 and counters["network_denied"] == 2
    assert not (tmp_path / "outside.db").exists()


def test_storage_uses_real_writer_preserves_values_and_no_duplicate(tmp_path):
    storage, quality, *_ = proof.load_runtime_modules()
    source = source_payload()
    original = deepcopy(source)
    path, report = proof.replay_storage(tmp_path, [source], storage, quality)
    assert source == original
    assert report["observations_preserved_exactly"]
    assert report["saved_counts"] == {"races": 1, "runners": 4, "dividends": 8}
    assert report["external_scrape_verified"] is False
    assert path.is_file()


def test_acquisition_actual_store_lease_stop_fence_and_recovery(tmp_path):
    _, _, saga, *_ = proof.load_runtime_modules()
    report = proof.replay_scrape_ledger(tmp_path, saga, "1" * 64)
    assert report["passed"] and report["stale_fence_write_rejected"]
    assert report["expired_lease_reclaimed_same_effect_key"] and report["durable_stop_survives_restart"]


def test_ticket_fixture_is_independent_of_finish_odds_and_payout():
    source = source_payload()
    chosen = proof.fixed_tickets(source)
    source["return_tables"] = []
    for horse in source["horses"]:
        horse["finish_position"] = 100 - horse["horse_number"]
        horse["odds"] = 1000
    assert proof.fixed_tickets(source) == chosen
    assert {ticket["bet_type"] for ticket in chosen} == set(proof.TYPES)
    assert all(ticket["stake"] == 100 for ticket in chosen)


def test_official_settlement_wait_recovery_and_no_profitability_claim(tmp_path):
    storage, quality, _, _, _, performance = proof.load_runtime_modules()
    source = source_payload()
    database, _ = proof.replay_storage(tmp_path, [source], storage, quality)
    report = proof.settlement_replay(tmp_path, database, [source], performance)
    assert report["passed"] and report["status_counts"] == {"settled": 1}
    assert report["rows"][0]["official_return"] == 300  # Only the number-only wide 1–2 fixture hits.
    assert report["restart_reload_idempotent"] and report["missing_payout_not_fake_loss"]
    assert report["actual_purchase_records"] == 0
    assert report["roi_evaluation"] == "NOT_PERFORMED"
    assert report["live_offer_acceptance_verified"] is False


def test_unsettled_runner_stays_pending_not_zero_loss(tmp_path):
    storage, quality, _, _, _, performance = proof.load_runtime_modules()
    source = source_payload(finishes=(3, 1, None, 2))
    database, _ = proof.replay_storage(tmp_path, [source], storage, quality)
    report = proof.settlement_replay(tmp_path, database, [source], performance)
    assert report["status_counts"] == {"pending": 1}
    assert report["rows"][0]["official_return"] is None
    assert report["settled_all_fixture_races"] is False


def test_real_loader_snapshot_and_training_protocol_no_fit(tmp_path):
    storage, quality, _, local, eligibility, _ = proof.load_runtime_modules()
    source = source_payload()
    database, _ = proof.replay_storage(tmp_path, [source], storage, quality)
    report = proof.replay_training(tmp_path, database, [source], local, eligibility,
        ("active-test-fixture", "2" * 64))
    assert report["passed"] and report["loaded_rows"] == 4
    assert report["eligibility"]["counts"]["eligible"] == {"races": 1, "rows": 4}
    assert report["preparation_restart_fail_closed"] and report["stale_lease_rejected"]
    assert report["running_restart_fail_closed"] and report["new_fence_monotonic"]
    assert report["model_fit_executed"] is report["artifact_published"] is False
    assert report["approved_training_job_fit_executed"] is False
    assert report["separate_saved_data_smoke_fit"]["status"] == "NOT_RUN"


def test_bounded_real_lightgbm_fit_artifact_reload_strict_date_split(tmp_path):
    storage, quality, _, local, _, _ = proof.load_runtime_modules()
    sources = []
    for rid, day in (("202101010101", "20210101"), ("202101010102", "20210101"),
                     ("202101010201", "20210102"), ("202101010202", "20210102")):
        source = source_payload(rid)
        source["race_info"]["date"] = day
        for horse in source["horses"]:
            horse["finish_time"] = f"1:1{horse['horse_number']}.0"
        sources.append(source)
    database, _ = proof.replay_storage(tmp_path, sources, storage, quality)
    snapshot = local.create_sqlite_snapshot(database.resolve(), (tmp_path / "snapshots").resolve())
    report = proof.smoke_model_fit(tmp_path, snapshot.path)
    assert report["status"] == "PASS" and report["model_fit_executed"] is True
    assert report["train"]["to"] < report["holdout"]["date"]
    assert report["requested_base_features"] == 16 and report["requested_total_features"] == 47
    assert report["train_only_encoder"] and report["train_only_target_baseline"]
    assert report["heldout_used_for_tuning_or_early_stopping"] is False
    assert report["point_in_time_audit"]["future_leakage_rows"] == 0
    assert report["reload_exact_scores"] and report["reload_max_absolute_score_difference"] == 0
    assert report["artifact_sha256"] == proof.file_digest(tmp_path / "model-smoke.joblib")
    assert report["boost_rounds"] == 30 and report["num_threads"] == 2
    assert report["roi_evaluation"] == "NOT_PERFORMED"
    assert report["model_adopted"] is report["used_for_buying"] is False
    assert "win_roi_percent" not in report["workflow_smoke_metrics_only"]


def test_single_date_never_replaced_with_random_split(tmp_path, monkeypatch):
    storage, quality, _, local, _, _ = proof.load_runtime_modules()
    database, _ = proof.replay_storage(tmp_path, [source_payload()], storage, quality)
    snapshot = local.create_sqlite_snapshot(database.resolve(), (tmp_path / "snapshots").resolve())
    import lightgbm
    def forbidden_fit(*_args, **_kwargs):
        pytest.fail("Single-date cohort must not train with a random/leaky split")
    monkeypatch.setattr(lightgbm, "train", forbidden_fit)
    report = proof.smoke_model_fit(tmp_path, snapshot.path)
    assert report["status"] == "NOT_RUN" and report["model_fit_executed"] is False
    assert report["temporal_split_verified"] is False


@pytest.mark.parametrize("name", ["report.md", "../outside.json"])
def test_evidence_write_rejects_unscoped_or_nonjson(tmp_path, monkeypatch, name):
    root = tmp_path / "reports"
    monkeypatch.setattr(proof, "REPORT_ROOT", root)
    with pytest.raises(ValueError):
        proof.write_report({"release_complete": False}, root / name)


def test_evidence_never_overwrites_existing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(proof, "REPORT_ROOT", tmp_path)
    output = proof.write_report({"release_complete": False}, tmp_path / "evidence.json")
    with pytest.raises(FileExistsError):
        proof.write_report({"release_complete": True}, output)
    assert json.loads(output.read_text(encoding="utf-8"))["release_complete"] is False
