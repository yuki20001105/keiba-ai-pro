"""Separate reporting adapter: isolated toy artifacts/SQLite, never live writes."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from forward_runtime.ledger import RuntimeLedger
from forward_runtime.schedule import aware, canonical, sha256
from test_forward_runtime_ledger import capture, decision, official
from test_forward_runtime_schedule import evidence, race

REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "forward_paper_compatible_report", REPO / "scripts/report_forward_paper_compatible.py")
reporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reporter)
AS_OF = "2026-09-20T06:12:00Z"


def fixture_runtime(tmp_path, *, skip=False, pending=False, settle=True, integer_keys=True):
    root = tmp_path / "isolated-runtime"
    objects = root / "objects"
    objects.mkdir(parents=True)
    # Retain the frozen code/metric definition, but NEVER load the real model.
    original = reporter.read_json(REPO / "reports/generated/forward-runtime/h04-live-20260920-v2/protocol.json")
    protocol = deepcopy(original["protocol"])
    artifacts = {"model": b"isolated-model-not-executable", "calibrator": b"isolated-calibrator-not-executable",
                 "features": canonical({"feature_columns": protocol["feature_columns"]}).encode(),
                 "rule": canonical(reporter.RULE).encode()}
    hashes = {}
    for name, content in artifacts.items():
        digest = reporter.hashlib.sha256(content).hexdigest()
        (objects / digest).write_bytes(content)
        hashes[name] = digest
    protocol["artifact_hashes"] = hashes
    digest = sha256(protocol)
    (root / "protocol.json").write_text(canonical({"protocol": protocol, "protocol_sha256": digest}), encoding="utf-8")
    (root / "analysis-definition.json").write_text(canonical({
        "definition": reporter.evaluation_definition(), "protocol_sha256": digest,
        "outcomes_observed": 0, "frozen_at": "2026-09-19T14:57:17Z"}), encoding="utf-8")
    contract = {"protocol_sha256": digest, "artifact_hashes": hashes}
    clock = [aware("2026-09-20T05:54:40Z")]
    ledger = RuntimeLedger(root / "ledger.sqlite3", clock=lambda: clock[0])
    ledger.initialize(contract)
    lease = ledger.acquire_owner("isolated-test", ttl_seconds=300)
    rows = [race(), race(2), race(3), race(4, post="2026-09-20T14:00:00+09:00")]
    ledger.register_day("20260920", evidence(rows), rows, lease)
    assert ledger.claim_due(lease)["race_id"] == race()["race_id"]
    snapshot = capture(contract, odds=(1., 1.) if skip else (3., 2.))
    for runner, number, score in zip(snapshot["snapshot"]["runners"], (2, 10), (.8, .2)):
        runner.update(horse_number=number, score=score)
    snapshot["snapshot_sha256"] = sha256(snapshot["snapshot"])
    choice = {"decision": "skip", "reason": "insufficient_expected_return"} if skip else {**decision(), "horse_number": 2}
    ledger.record_capture(lease, race()["race_id"], snapshot, choice)
    ledger.record_terminal(lease, race(2)["race_id"], "skipped", "fixture-source-missing")
    ledger.record_terminal(lease, race(3)["race_id"], "failed", "fixture-transport-failure")
    ledger.release_owner(lease)
    clock[0] = aware("2026-09-20T06:11:00Z")
    if settle:
        lease = ledger.acquire_owner("isolated-settlement", ttl_seconds=300)
        ranks = {2: 1, 10: 2} if integer_keys else {"2": 1, "10": 2}
        result = official(runner_numbers=[2, 10], winners=[2], win_payouts={"2": 270}, ranks=ranks,
                          complete=not pending, status="pending" if pending else "final")
        ledger.record_settlement(lease, race()["race_id"], result)
        ledger.release_owner(lease)
    return root, digest, ledger


def evaluate(fixture):
    root, digest, _ = fixture
    return reporter.build_report(root, as_of=AS_OF, expected_protocol=digest)


def test_exact_legacy_report_restored_without_mutation_or_guard_override(tmp_path):
    fixture = fixture_runtime(tmp_path)
    root, _, ledger = fixture
    original = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    result = evaluate(fixture)
    assert result["status"] == "PASS", result.get("failure")
    assert result["strict_original_report_guard"]["status"] == "FAIL"
    assert result["strict_original_report_guard"]["reason"] == "aggregate-settlement-hash-or-binding-mismatch"
    audit = result["consistent_view_audit"]
    assert audit["settlement_hash_modes"] == {"exact_original_integer_rank_keys": 1}
    assert audit["event_hash_modes"]["exact_original_integer_rank_keys"] == 1
    assert audit["rank_key_compatibility_race_ids"] == [race()["race_id"]]
    assert audit["races_verified"] == 4 and audit["days_verified"] == 90
    assert result["semantic_replay_audit"]["pre_race_captures_verified"] == 1
    evaluation = result["prediction_and_betting_evaluation"]
    assert evaluation["prediction"]["rank_correlation"] == 1
    assert evaluation["prediction"]["normalized_rank_rmse"] == pytest.approx(.2)
    betting = evaluation["paper_betting"]
    assert betting["scheduled_races_known"] == 4
    assert betting["failures"] == 1 and betting["missed"] == 1 and betting["skipped"] == 1
    assert betting["paper_tickets"] == 1 and betting["stake_yen"] == 100
    assert betting["payout_yen"] == 270 and betting["gross_return_percent"] == 270
    assert result["actual_purchases"] == 0 and result["metrics_computed"] is True
    assert result["guard_bypassed"] is False and result["app_performance_db_modified"] is False
    assert result["operational_performance_validated"] is False
    assert result["fixed_inputs_audit"]["model_loaded"] is False
    assert original == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    # The frozen strict evaluator STILL rejects the unchanged original ledger.
    with pytest.raises(reporter.ForwardError, match="aggregate-settlement-hash-or-binding-mismatch"):
        reporter.evaluate_forward(ledger.snapshot(), as_of=AS_OF)


def test_native_string_ranks_need_no_compatibility_and_strict_guard_passes(tmp_path):
    result = evaluate(fixture_runtime(tmp_path, integer_keys=False))
    assert result["status"] == "PASS", result.get("failure")
    assert result["strict_original_report_guard"]["status"] == "PASS"
    assert result["consistent_view_audit"]["rank_key_compatibility_race_ids"] == []


def test_zero_investment_is_undefined_roi_not_success_or_zero_loss(tmp_path):
    result = evaluate(fixture_runtime(tmp_path, skip=True))
    assert result["status"] == "PASS", result.get("failure")
    betting = result["prediction_and_betting_evaluation"]["paper_betting"]
    assert betting["paper_tickets"] == 0 and betting["stake_yen"] == 0
    assert betting["gross_return_percent"] is None and betting["net_roi_percent"] is None
    assert betting["known_subset"]["gross_return_percent"] is None
    assert result["prediction_and_betting_evaluation"]["prediction"]["probability_races"] == 1


@pytest.mark.parametrize("settle", [False, True])
def test_pending_not_mixed_with_settled_roi_or_assumed_zero_payout(tmp_path, settle):
    result = evaluate(fixture_runtime(tmp_path, pending=True, settle=settle))
    assert result["status"] == "PASS", result.get("failure")
    evaluation = result["prediction_and_betting_evaluation"]
    betting = evaluation["paper_betting"]
    assert betting["paper_tickets"] == 1 and betting["pending_tickets"] == 1
    assert betting["stake_yen"] == 100 and betting["payout_yen"] is None
    assert betting["gross_return_percent"] is None and betting["known_subset"]["tickets"] == 0
    assert evaluation["prediction"]["probability_races"] == 0


@pytest.mark.parametrize("tamper", ["settlement_amount", "status", "reason", "eligible", "captured_at", "settled_at", "days"])
def test_hash_or_unhashed_projection_discrepancies_fail_closed(tmp_path, tamper):
    fixture = fixture_runtime(tmp_path)
    root, _, _ = fixture
    with sqlite3.connect(root / "ledger.sqlite3") as db:
        if tamper == "settlement_amount":
            settlement = json.loads(db.execute("SELECT settlement_json FROM races WHERE race_id=?", (race()["race_id"],)).fetchone()[0])
            settlement["payout_yen"] = 100000
            db.execute("UPDATE races SET settlement_json=? WHERE race_id=?", (canonical(settlement), race()["race_id"]))
        elif tamper == "days":
            db.execute("UPDATE schedule_days SET status='not_held' WHERE date='20260921'")
        else:
            values = {"status": "failed", "reason": "forged-failure", "eligible": 0,
                      "captured_at": AS_OF, "settled_at": AS_OF}
            db.execute(f"UPDATE races SET {tamper}=? WHERE race_id=?", (values[tamper], race()["race_id"]))
    result = evaluate(fixture)
    assert result["status"] == "FAIL"
    assert result["metrics_computed"] is False
    assert result["prediction_and_betting_evaluation"] is None


@pytest.mark.parametrize("tamper", ["model_bytes", "analysis", "protocol_pin"])
def test_changed_frozen_inputs_never_authorize_comparison(tmp_path, tamper):
    fixture = fixture_runtime(tmp_path)
    root, digest, _ = fixture
    if tamper == "model_bytes":
        protocol = reporter.read_json(root / "protocol.json")["protocol"]
        (root / "objects" / protocol["artifact_hashes"]["model"]).write_bytes(b"changed")
    elif tamper == "analysis":
        value = reporter.read_json(root / "analysis-definition.json")
        value["outcomes_observed"] = 1
        (root / "analysis-definition.json").write_text(canonical(value), encoding="utf-8")
    else:
        fixture = root, "f" * 64, fixture[2]
    result = evaluate(fixture)
    assert result["status"] == "FAIL" and result["metrics_computed"] is False
    assert result["prediction_and_betting_evaluation"] is None


def test_new_unknown_event_kind_even_with_exact_hash_is_not_interpreted(tmp_path):
    root, _, _ = fixture_runtime(tmp_path)
    raw = reporter.read_consistent_view(root / "ledger.sqlite3")
    last = raw["events"][-1]
    body = {"recorded_at": AS_OF, "race_id": None, "event_type": "unknown-new-rule",
            "payload": {}, "previous_sha256": last["event_sha256"]}
    raw["events"].append({"seq": last["seq"] + 1, "payload_json": canonical({}),
                          **{k: v for k, v in body.items() if k != "payload"}, "event_sha256": sha256(body)})
    with pytest.raises(reporter.ForwardError, match="unsupported-event-type"):
        reporter.audit_consistent_view(raw)


def test_historical_time_cannot_drop_future_events_or_denominators(tmp_path):
    root, digest, _ = fixture_runtime(tmp_path)
    result = reporter.build_report(root, as_of="2026-09-20T05:54:00Z", expected_protocol=digest)
    assert result["status"] == "FAIL"
    assert result["failure"]["reason"] == "historical-as-of-projection-reconstruction-not-supported"


def test_output_refuses_overwrite_and_frozen_root(tmp_path):
    root, _, _ = fixture_runtime(tmp_path)
    existing = tmp_path / "already-existing.json"
    existing.write_text("preserve", encoding="utf-8")
    with pytest.raises(SystemExit):
        reporter.main(["--root", str(root), "--output", str(existing)])
    assert existing.read_text(encoding="utf-8") == "preserve"
    with pytest.raises(SystemExit):
        reporter.main(["--root", str(root), "--output", str(root / "new-report.json")])
    assert not (root / "new-report.json").exists()
