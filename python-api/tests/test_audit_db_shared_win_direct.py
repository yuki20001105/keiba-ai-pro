"""External audit helper tests; no research model is fitted."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import audit_db_shared_win_direct as audit
import db_shared_blend46_betting as betting
import db_shared_win_direct_worker as worker


def cohort():
    rows = []
    for day, win in (("20230105", 1), ("20230112", 2), ("20240106", 3), ("20240113", 1)):
        for number in range(1, 4):
            rows.append({"race_id": day[:4] + "050101" + day[-2:], "race_date": day,
                "horse_number": number, "finish": 1 if number == win else 2 + [n for n in range(1, 4) if n != win].index(number),
                "probability": (.5, .3, .2)[number - 1], "raw_score": 3. - number, "odds": (3., 10., 20.)[number - 1],
                "expected_runner_count": 3, "venue": "東京", "surface": "turf",
                "tansho_payout": (420., 1300., 2400.)[number - 1] if number == win else np.nan})
    return pd.DataFrame(rows)


@pytest.fixture
def frame():
    return cohort()


def artifact_run(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    file = run / "model.json"
    file.write_text('{"value":1}', encoding="utf-8")
    manifest = {"files": {"model.json": {"sha256": audit.sha(file), "bytes": file.stat().st_size}}}
    (run / "artifact-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return run, file, manifest


def test_manifest_verifies_all_hashes_and_bytes(tmp_path):
    run, _, manifest = artifact_run(tmp_path)
    assert audit.verify_manifest(run, exact_files=True) == manifest


def test_manifest_same_size_tampering_rejected(tmp_path):
    run, file, _ = artifact_run(tmp_path)
    file.write_text('{"value":2}', encoding="utf-8")
    with pytest.raises(ValueError, match="file-changed"):
        audit.verify_manifest(run)


def test_manifest_wrong_size_rejected(tmp_path):
    run, file, _ = artifact_run(tmp_path)
    file.write_text("short", encoding="utf-8")
    with pytest.raises(ValueError, match="file-changed"):
        audit.verify_manifest(run)


def test_manifest_hash_binding_rejected(tmp_path):
    run, _, _ = artifact_run(tmp_path)
    with pytest.raises(ValueError, match="manifest-hash"):
        audit.verify_manifest(run, "0" * 64)


def test_legacy_windows_manifest_read_only_normalization(tmp_path):
    run, _, manifest = artifact_run(tmp_path)
    nested = run / "folder"
    nested.mkdir()
    item = nested / "saved.json"
    item.write_text("{}", encoding="utf-8")
    manifest["files"]["folder\\saved.json"] = {"sha256": audit.sha(item), "bytes": item.stat().st_size}
    (run / "artifact-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="relative-path"):
        audit.verify_manifest(run)
    assert audit.verify_manifest(run, legacy_windows_paths=True) == manifest
    manifest["files"]["..\\escape.json"] = manifest["files"]["folder\\saved.json"]
    (run / "artifact-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="relative-path"):
        audit.verify_manifest(run, legacy_windows_paths=True)


def test_extra_file_in_sealed_run_rejected(tmp_path):
    run, _, _ = artifact_run(tmp_path)
    (run / "late.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="extra-or-missing"):
        audit.verify_manifest(run, exact_files=True)


@pytest.mark.parametrize("relative", ["../escape.json", "..\\escape.json", "C:/escape.json", "/escape.json", ""])
def test_manifest_paths_never_escape(tmp_path, relative):
    with pytest.raises(ValueError, match="relative-path"):
        audit.bounded(tmp_path, relative)


def test_model_artifact_same_size_tampering_rejected(tmp_path):
    run, file, _ = artifact_run(tmp_path)
    receipt = {"path": str(file), "sha256": audit.sha(file)}
    assert audit.verified_artifact(run, receipt) == file.resolve()
    file.write_text('{"value":2}', encoding="utf-8")
    with pytest.raises(ValueError, match="worker-artifact-changed"):
        audit.verified_artifact(run, receipt)


def test_model_artifact_outside_run_rejected(tmp_path):
    run, _, _ = artifact_run(tmp_path)
    file = tmp_path / "outside.json"
    file.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="worker-artifact-changed"):
        audit.verified_artifact(run, {"path": str(file), "sha256": audit.sha(file)})


def test_prediction_key_count_rejected(frame):
    with pytest.raises(ValueError, match="cohort-count"):
        audit.prediction_match(frame.iloc[1:], frame, {"probability": frame.probability.to_numpy()})


def test_prediction_changed_key_rejected(frame):
    altered = frame.copy()
    altered.loc[0, "horse_number"] = 18
    with pytest.raises(ValueError, match="cohort-values"):
        audit.prediction_match(altered, frame, {"probability": frame.probability.to_numpy()})


def test_prediction_changed_probability_rejected(frame):
    values = frame.probability.to_numpy().copy()
    values[0] += 1e-12
    with pytest.raises(ValueError, match="replay-difference"):
        audit.prediction_match(frame, frame, {"probability": values})


def test_prediction_exact_replay(frame):
    assert audit.prediction_match(frame, frame, {"probability": frame.probability.to_numpy()}) == {"probability": 0.}


@pytest.mark.parametrize("rule", audit.RULES)
def test_independent_policy_keys_match_fixed_recipe(frame, rule):
    actual = audit.policy_keys(frame, rule)
    expected = betting.select_bets(frame, rule).sort_values(audit.KEYS).reset_index(drop=True)
    pd.testing.assert_frame_equal(actual[audit.KEYS], expected[audit.KEYS])
    assert actual.stake.eq(100.).all()


def test_policy_keys_do_not_read_outcomes(frame):
    expected = audit.policy_keys(frame, "max_ev105")
    poison = frame.drop(columns=["finish", "tansho_payout"]).assign(future_payout=999999)
    pd.testing.assert_frame_equal(expected, audit.policy_keys(poison, "max_ev105"))


def test_independent_settlement_not_odds_multiplication(frame):
    tickets = audit.policy_keys(frame, "top1")
    actual = audit.independent_settlement(frame, tickets)
    assert actual.payout.sum() == 840.
    assert frame.loc[frame.finish.eq(1) & frame.horse_number.eq(1), "odds"].sum() * 100 != actual.payout.sum()
    pd.testing.assert_frame_equal(actual[audit.KEYS + ["stake", "payout", "profit"]].reset_index(drop=True),
        betting.settle_bets(frame, tickets)[audit.KEYS + ["stake", "payout", "profit"]].reset_index(drop=True))


@pytest.mark.parametrize("rule", audit.RULES)
def test_independent_summary_matches_money_and_full_date_ledger(frame, rule):
    tickets = audit.policy_keys(frame, rule)
    summary = audit.settlement_summary(frame, audit.independent_settlement(frame, tickets))
    reference = betting.summarize_policy(frame, tickets)
    audit.compare_summary(summary, reference)


def test_unknown_ticket_cannot_become_losing_ticket(frame):
    tickets = audit.policy_keys(frame, "top1")
    tickets.loc[0, "horse_number"] = 18
    with pytest.raises(ValueError, match="outside-cohort"):
        audit.independent_settlement(frame, tickets)


def test_duplicate_ticket_cannot_double_money(frame):
    ticket = audit.policy_keys(frame, "top1").iloc[:1]
    with pytest.raises(ValueError, match="unique-100"):
        audit.independent_settlement(frame, pd.concat([ticket, ticket]))


def test_no_bets_keep_full_date_ledger_and_null_roi(frame):
    empty = audit.policy_keys(frame, "top1").iloc[:0]
    summary = audit.settlement_summary(frame, audit.independent_settlement(frame, empty))
    assert summary["roi_percent"] is None and summary["stake"] == 0.
    assert len(summary["daily_ledger"]) == 4
    audit.compare_summary(summary, betting.summarize_policy(frame, empty))


def test_frozen_choice_verifier_uses_only_select_ci_and_minimums():
    rows = [{"policy_id": "market46:top1", "summary": {"bets": 500, "dates_with_bets": 30,
        "yearly": [{"year": 2023, "bets": 250}, {"year": 2024, "bets": 250}], "roi_percent": 90.},
        "roi_ci_simultaneous": [80., 100.]}]
    best = {"policy_id": "market46:top1", "lower_roi_bound": 80., "roi_percent": 90., "bets": 500}
    frozen = {"highest_candidate": best, "eligible_candidates": [best], "selected_policy_id": None,
        "test_outcomes_consumed": False, "diagnostic_rule": "top1", "diagnostic_rule_is_purchase_authority": False}
    assert audit.verify_selection(frozen, rows, "market46")["selected_policy_id"] is None
    changed = deepcopy(frozen)
    changed["selected_policy_id"] = best["policy_id"]
    with pytest.raises(ValueError, match="frozen-choice"):
        audit.verify_selection(changed, rows, "market46")


def test_audit_publication_outside_study_and_exclusive(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    generated = tmp_path / "reports/generated"
    generated.mkdir(parents=True)
    run = generated / "study"
    run.mkdir()
    out = generated / "audit.json"
    audit.publish_audit(out, {"status": "passed"}, run)
    assert json.loads(out.read_text()) == {"status": "passed"}
    with pytest.raises(FileExistsError):
        audit.publish_audit(out, {"status": "replaced"}, run)
    with pytest.raises(ValueError, match="outside-study"):
        audit.publish_audit(run / "audit.json", {}, run)


def request_fixture(tmp_path):
    frame_path = tmp_path / "input.parquet"
    frame_path.write_bytes(b"pinned-input-fixture")
    columns, categorical = worker.feature_contract("market_only")
    request = {"schema": worker.SCHEMA, "job_id": "market_only", "candidate_id": "market_only",
        "frame_path": str(frame_path), "frame_sha256": audit.sha(frame_path), "protocol_sha256": "1" * 64,
        "expected_source_hashes": {"fixed.py": "2" * 64}, "feature_columns": columns,
        "categorical_columns": categorical, "device": "cpu", "n_threads": 2}
    result = {"schema": worker.SCHEMA, "status": "completed", "job_id": "market_only", "candidate_id": "market_only",
        "request_sha256": audit.object_hash(request), "source_hashes": request["expected_source_hashes"],
        "protocol_sha256": request["protocol_sha256"], "frame_sha256": request["frame_sha256"],
        "feature_columns": columns, "categorical_columns": categorical, "feature_count": 4,
        "periods": {name: list(bounds) for name, bounds in worker.PERIODS.items()},
        "runtime": {"parameters": worker.PARAMETERS.copy(), "rounds": worker.BOOST_ROUNDS}}
    return request, result, frame_path


def test_request_binding_accepts_only_exact_receipt(tmp_path):
    request, result, frame_path = request_fixture(tmp_path)
    audit.verify_request(request, result, "1" * 64, {"fixed.py": "2" * 64}, frame_path)


@pytest.mark.parametrize("field,value", [("request_sha256", "0" * 64), ("feature_count", 50),
    ("protocol_sha256", "3" * 64), ("feature_columns", ["odds"]), ("job_id", "wrong")])
def test_request_binding_tampering_rejected(tmp_path, field, value):
    request, result, frame_path = request_fixture(tmp_path)
    result[field] = value
    with pytest.raises(ValueError, match="request-binding"):
        audit.verify_request(request, result, "1" * 64, {"fixed.py": "2" * 64}, frame_path)

