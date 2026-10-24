"""Synthetic Phase 1 fixtures: these tests are not real live provenance."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import run_db_shared_win_pipeline as pipeline


def result_html(*, winner=1, payout=490, count=18):
    rows = []
    for number in range(1, count + 1):
        finish = 1 if number == winner else winner if number == 1 else number
        rows.append(f'<tr><td>{finish}</td><td>1</td><td>{number}</td>'
                    f'<td><a href="https://db.netkeiba.com/horse/20190000{number:02d}/">Horse {number}</a></td></tr>')
    return ('<html><head><meta charset="utf-8"><title>スワンS | 2026年10月12日 京都11R</title>'
            '<link rel="canonical" href="https://race.netkeiba.com/race/result.html?race_id=202608040511"></head>'
            '<body><div class="RaceData01">15:45発走 / 芝1400m</div><table class="RaceTable01">'
            + ''.join(rows) + '</table><table class="Payout_Detail_Table">'
            f'<tr class="Tansho"><td class="Result"><span>{winner}</span></td>'
            f'<td class="Payout"><span>{payout:,}円</span></td></tr></table></body></html>')


def result_source(html):
    return {"url": "https://race.netkeiba.com/race/result.html?race_id=202608040511",
            "request_url": "https://race.netkeiba.com/race/result.html?race_id=202608040511",
            "status": 200, "source": "network", "request_started_at": "2026-10-12T06:50:00Z",
            "observed_at": "2026-10-12T06:50:02Z", "body_sha256": pipeline.digest(html.encode()),
            "body_size": len(html.encode())}


def card():
    return {"mode": "live", "race_id": "202608040511", "post_time": "2026-10-12T06:45:00Z",
            "captured_at": "2026-10-12T06:39:05Z", "capture_validation": {"point_in_time_verified": True, "reason_codes": []},
            "card": {"race_info": {"date": "20261012", "venue": "京都", "track_type": "芝", "distance": 1400, "num_horses": 18},
                     "horses": [{"horse_number": n, "horse_id": f"20190000{n:02d}"} for n in range(1, 19)]},
            "source_receipts": [{"kind": kind, "status": 200, "source": "network",
                                 "request_started_at": "2026-10-12T06:39:00Z", "observed_at": "2026-10-12T06:39:02Z"}
                                for kind in ("card", "odds")]}


def predictions():
    values = [{"horse_number": n, "horse_id": f"20190000{n:02d}", "race_id": "202608040511",
               "race_date": "20261012", "probability": 1 / 18, "odds": 4.9} for n in range(1, 19)]
    return {"market46": values}


def decision():
    return pipeline.select_paper_decision(predictions())


def normalize(html=None, source=None, choice=None, bundle=None):
    from scraping import race
    html = html or result_html()
    return pipeline.normalize_settlement(html, race.parse_current_result_snapshot(html, pipeline.TARGET["race_id"]),
                                         source or result_source(html), choice or decision(), bundle or card())


def test_fixed_plan_target_window_and_no_actual_authority():
    policy = pipeline.prestart.CapturePolicy(**pipeline.POLICY)
    opens, deadline = policy.window(pipeline.instant(pipeline.TARGET["post_time"]))
    assert pipeline.iso(opens) == "2026-10-12T06:39:00Z"
    assert pipeline.iso(deadline) == "2026-10-12T06:41:00Z"
    selected = decision()
    assert selected["horse_number"] == 1
    assert selected["stake_yen"] == 100
    assert selected["paper_only"] and not selected["purchase_authorized"]
    assert not selected["expected_value_authorized"] and selected["actual_purchases"] == 0


@pytest.mark.parametrize("change", ["partial", "duplicate_number", "duplicate_id", "wrong_race", "wrong_date", "nan", "zero", "oversum", "bad_odds"])
def test_paper_rule_rejects_incomplete_or_invalid_inputs(change):
    data = predictions()
    rows = data["market46"]
    if change == "partial": rows.pop()
    if change == "duplicate_number": rows[-1]["horse_number"] = 1
    if change == "duplicate_id": rows[-1]["horse_id"] = rows[0]["horse_id"]
    if change == "wrong_race": rows[0]["race_id"] = "202608040512"
    if change == "wrong_date": rows[0]["race_date"] = "20261011"
    if change == "nan": rows[0]["probability"] = float("nan")
    if change == "zero": rows[0]["probability"] = 0
    if change == "oversum": rows[0]["probability"] = .9
    if change == "bad_odds": rows[0]["odds"] = .1
    with pytest.raises(pipeline.PipelineError):
        pipeline.select_paper_decision(data)


@pytest.mark.parametrize("change", ["replay", "outside_window", "cached", "late_source", "wrong_venue", "wrong_date", "partial", "wrong_id", "bad_validation", "wrong_surface", "wrong_distance", "wrong_post", "duplicate_source", "duplicate_horse_id", "duplicate_number"])
def test_current_capture_gate_is_strict(change):
    data = card()
    if change == "replay": data["mode"] = "sealed_replay"
    if change == "outside_window": data["captured_at"] = "2026-10-12T06:41:01Z"
    if change == "cached": data["source_receipts"][0]["source"] = "cache"
    if change == "late_source": data["source_receipts"][0]["observed_at"] = "2026-10-12T06:42:00Z"
    if change == "wrong_venue": data["card"]["race_info"]["venue"] = "東京"
    if change == "wrong_date": data["card"]["race_info"]["date"] = "20261011"
    if change == "partial": data["card"]["horses"].pop()
    if change == "wrong_id": data["race_id"] = "202608040512"
    if change == "bad_validation": data["capture_validation"]["point_in_time_verified"] = False
    if change == "wrong_surface": data["card"]["race_info"]["track_type"] = "ダート"
    if change == "wrong_distance": data["card"]["race_info"]["distance"] = 1600
    if change == "wrong_post": data["post_time"] = "2026-10-12T06:40:00Z"
    if change == "duplicate_source": data["source_receipts"].append(deepcopy(data["source_receipts"][0]))
    if change == "duplicate_horse_id": data["card"]["horses"][-1]["horse_id"] = data["card"]["horses"][0]["horse_id"]
    if change == "duplicate_number": data["card"]["horses"][-1]["horse_number"] = 1
    with pytest.raises(pipeline.PipelineError):
        pipeline.validate_capture(data)


def test_actual_payout_win_and_loss_settle_without_odds_proxy():
    won = normalize()
    assert won["paper_hit"] and won["paper_return_yen"] == 490
    assert won["paper_profit_yen"] == 390 and won["paper_roi_percent"] == 490
    assert not won["settlement_from_final_odds"] and won["actual_purchases"] == 0
    assert won["actual_settlement_verified"]
    lost = normalize(result_html(winner=2, payout=750))
    assert not lost["paper_hit"] and lost["paper_return_yen"] == 0
    assert lost["actual_tansho_payout_per_100_yen"] == 750
    assert lost["paper_profit_yen"] == -100


@pytest.mark.parametrize("change", ["wrong_url", "wrong_host", "wrong_request", "cache", "http_error", "before_post", "reverse_clock", "wrong_day", "wrong_canonical", "wrong_post", "no_post", "partial", "wrong_horse", "no_horse", "duplicate_number", "nonfinish", "dead_heat", "wrong_payout_number", "no_payout", "zero_payout"])
def test_result_identity_date_whole_field_and_literal_payout_gates(change):
    html = result_html()
    source = result_source(html)
    if change == "wrong_url": source["url"] = source["url"].replace("202608040511", "202608040512")
    if change == "wrong_host": source["url"] = source["url"].replace("race.netkeiba.com", "evil.example")
    if change == "wrong_request": source["request_url"] = source["request_url"].replace("202608040511", "202608040512")
    if change == "cache": source["source"] = "cache"
    if change == "http_error": source["status"] = 403
    if change == "before_post": source["request_started_at"] = "2026-10-12T06:44:00Z"
    if change == "reverse_clock": source["observed_at"] = "2026-10-12T06:49:00Z"
    if change == "wrong_day": html = html.replace("2026年10月12日", "2026年10月11日")
    if change == "wrong_canonical": html = html.replace("race_id=202608040511", "race_id=202608040512")
    if change == "wrong_post": html = html.replace("15:45発走", "15:40発走")
    if change == "no_post": html = html.replace("15:45発走", "発走")
    if change == "partial": html = result_html(count=17)
    if change == "wrong_horse": html = html.replace("2019000001/", "2019000099/")
    if change == "no_horse": html = html.replace("/horse/2019000001/", "/profile/2019000001/")
    if change == "duplicate_number": html = html.replace("<td>18</td>", "<td>17</td>")
    if change == "nonfinish": html = html.replace("<tr><td>18</td>", "<tr><td>取消</td>")
    if change == "dead_heat": html = html.replace("<tr><td>18</td>", "<tr><td>1</td>")
    if change == "wrong_payout_number": html = html.replace('<td class="Result"><span>1</span>', '<td class="Result"><span>2</span>')
    if change == "no_payout": html = html.replace('class="Tansho"', 'class="Unknown"')
    if change == "zero_payout": html = html.replace("490円", "0円")
    with pytest.raises(pipeline.PipelineError):
        normalize(html, source)


def test_result_snapshot_must_be_actual_parser_output():
    html = result_html()
    with pytest.raises(pipeline.PipelineError, match="parser_mismatch"):
        pipeline.normalize_settlement(html, {"horses": [{"horse_number": 18, "finish_position": 1}]}, result_source(html), decision(), card())


def test_sealed_stage_detects_corrupt_and_unsealed_files(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    pipeline.write_new(stage / "receipt.json", pipeline.body({"test_fixture_not_live": True}))
    pipeline.seal(stage, "fixture")
    pipeline.verify_seal(stage, "fixture")
    (stage / "extra.json").write_text("{}")
    with pytest.raises(pipeline.PipelineError, match="unsealed"):
        pipeline.verify_seal(stage, "fixture")
    (stage / "extra.json").unlink()
    (stage / "receipt.json").write_text("{}")
    with pytest.raises(pipeline.PipelineError, match="hash_mismatch"):
        pipeline.verify_seal(stage, "fixture")


def test_missing_network_authority_never_imports_or_fetches():
    with pytest.raises(pipeline.PipelineError, match="explicit_allow"):
        asyncio.run(pipeline.capture(Path("not-a-plan")))
    with pytest.raises(pipeline.PipelineError, match="explicit_allow"):
        asyncio.run(pipeline.settle(Path("not-a-plan")))


def test_missing_verified_capture_blocks_result_network(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(pipeline, "safe_plan", lambda path: Path(path))
    monkeypatch.setattr(pipeline, "read_plan", lambda _path: {"plan_directory": str(tmp_path), "plan_sha256": "0" * 64, "plan": {}})
    monkeypatch.setattr(pipeline, "stages", lambda _path: [])
    async def fetch(_path):
        called.append(True)
    monkeypatch.setattr(pipeline, "acquire_result", fetch)
    with pytest.raises(pipeline.PipelineError, match="before_outcome_access"):
        asyncio.run(pipeline.settle(tmp_path, allow_network=True))
    assert not called and not (tmp_path / ".pipeline.lock").exists()


def test_fake_true_flag_proof_is_not_gate_authority(monkeypatch, tmp_path):
    fake = {"schema": pipeline.PROOF_SCHEMA, "phase1_pass": True, "status": "completed"}
    (tmp_path / "proof.json").write_text(json.dumps(fake))
    (tmp_path / "proof-manifest.json").write_text("{}")
    monkeypatch.setattr(pipeline, "_proof_payload", lambda _path: {"schema": pipeline.PROOF_SCHEMA, "status": "incomplete", "phase1_pass": False})
    with pytest.raises(pipeline.PipelineError, match="does_not_recompute"):
        pipeline.verify_gate_proof(tmp_path / "proof.json")


def test_incomplete_proof_is_never_published(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "_proof_payload", lambda _path: {"schema": pipeline.PROOF_SCHEMA, "status": "incomplete", "phase1_pass": False})
    result = pipeline.verify(tmp_path, publish=True)
    assert not result["phase1_pass"]
    assert not (tmp_path / "proof.json").exists()


def test_gate_recompute_and_published_manifest_bind_all_evidence(monkeypatch, tmp_path):
    # Pure synthetic envelope test: no live or profitability claim is produced.
    payload = {"schema": pipeline.PROOF_SCHEMA, "status": "completed", "phase1_pass": True,
               "plan_sha256": "a" * 64, "capture_stage_manifest_sha256": "b" * 64,
               "settlement_stage_manifest_sha256": "c" * 64, "synthetic_test_only": True}
    monkeypatch.setattr(pipeline, "_proof_payload", lambda _path: deepcopy(payload))
    assert pipeline.verify(tmp_path, publish=True) == payload
    first = pipeline.verify_gate_proof(tmp_path / "proof.json")
    assert first["proof"] == payload
    assert pipeline.verify(tmp_path, publish=True) == payload
    (tmp_path / "proof-manifest.json").write_text("{}")
    with pytest.raises(pipeline.PipelineError, match="manifest_mismatch"):
        pipeline.verify_gate_proof(tmp_path / "proof.json")


def test_exclusive_lock_is_not_stolen_and_owned_lock_is_released(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "safe_plan", lambda path: Path(path))
    with pipeline.exclusive(tmp_path):
        assert (tmp_path / ".pipeline.lock").exists()
        with pytest.raises(FileExistsError):
            with pipeline.exclusive(tmp_path):
                pass
    assert not (tmp_path / ".pipeline.lock").exists()


def test_stage_publication_clock_is_after_main_seal(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "read_plan", lambda _path: {"plan_sha256": "a" * 64})
    times = iter([datetime(2026, 10, 12, 6, 39, second, tzinfo=timezone.utc) for second in range(10)])
    monkeypatch.setattr(pipeline, "now", lambda: next(times))
    directory, receipt = pipeline._new_stage(tmp_path, "capture")
    receipt["status"] = "failed"
    published = pipeline._publish_stage(directory, receipt)
    assert pipeline.instant(published["receipt"]["completed_at"]) < pipeline.instant(published["publication"]["published_at"])
    assert pipeline.verify_seal(Path(published["directory"]), pipeline.SCHEMA + "-stage") == published["manifest_sha256"]
    assert (tmp_path / "publications" / published["manifest_sha256"] / "artifact_manifest.json").exists()


def test_frozen_freshness_receipt_acknowledges_not_operationally_complete():
    result = pipeline.freshness_receipt(pipeline.DEFAULT_FRESHNESS)
    assert result["eligible_history_max_source_date"] == "20260913"
    assert not result["history_freshness_complete"] and not result["current_career_coverage_verified"]
    assert result["gap_acknowledged_not_imputed"] and result["not_ev_or_buy_authority"]
    assert result["sha256"] == "d4a01f7ea3914102c48f317b4f9cbfb7b42c632c63a24d2b1f630ed743653f65"


def test_recovery_does_not_release_live_owner(monkeypatch, tmp_path):
    import os
    import psutil
    monkeypatch.setattr(pipeline, "safe_plan", lambda path: Path(path))
    monkeypatch.setattr(pipeline, "read_plan", lambda _path: {"plan_sha256": "a" * 64})
    raw = pipeline.body({"pid": os.getpid(), "pid_created_at_epoch": psutil.Process().create_time()})
    (tmp_path / ".pipeline.lock").write_bytes(raw)
    with pytest.raises(pipeline.PipelineError, match="live_pipeline_owner"):
        pipeline.recover_lock(tmp_path)
    assert (tmp_path / ".pipeline.lock").read_bytes() == raw


def test_recovery_releases_only_verified_orphan_without_network(monkeypatch, tmp_path):
    import psutil
    monkeypatch.setattr(pipeline, "safe_plan", lambda path: Path(path))
    monkeypatch.setattr(pipeline, "read_plan", lambda _path: {"plan_sha256": "a" * 64})
    (tmp_path / ".pipeline.lock").write_bytes(pipeline.body({"pid": 2147483647, "pid_created_at_epoch": 100.}))
    def absent(_pid):
        raise psutil.NoSuchProcess(_pid)
    monkeypatch.setattr(psutil, "Process", absent)
    result = pipeline.recover_lock(tmp_path)
    assert result["status"] == "orphan_lock_recovered"
    assert result["network_requests"] == result["processes_stopped"] == result["race_or_model_database_writes"] == 0
    assert result["interrupted_stage_objects_preserved"]
    assert not (tmp_path / ".pipeline.lock").exists()
    pipeline.verify_seal(Path(result["recovery_directory"]), pipeline.SCHEMA + "-lock-recovery")


def test_stage_without_publication_is_quarantined_not_authorized(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "read_plan", lambda _path: {"plan_sha256": "a" * 64})
    stage = tmp_path / "stages/unfinished-publication"
    stage.mkdir(parents=True)
    pipeline.write_new(stage / "stage.json", pipeline.body({"schema": pipeline.SCHEMA + "-stage",
                       "plan_sha256": "a" * 64, "stage": "capture", "status": "completed"}))
    pipeline.seal(stage, pipeline.SCHEMA + "-stage")
    rows = pipeline.stages(tmp_path)
    assert rows[0]["receipt"]["status"] == "quarantined"
    assert rows[0]["receipt"]["unconfirmed_stage_status"] == "completed"
    assert rows[0]["publication"] is None
    assert (stage / "stage.json").exists()


def synthetic_capture_stage(monkeypatch, tmp_path):
    """Exercise real stage-clock checks with synthetic model/parser seams."""
    import verify_db_shared_win_prestart_inference as inference
    plan = tmp_path / "plan"
    stage = plan / "stages/teststage"
    captured = plan / "capture-inputs/fixture"
    stage.mkdir(parents=True)
    captured.mkdir(parents=True)
    (captured / "artifact_manifest.json").write_bytes(b"synthetic only")
    (captured / "sources").mkdir()
    (captured / "sources/history.parquet").write_bytes(b"synthetic-history")
    source = card()
    source["history_audit"] = {"prepared_state_sha256": "1" * 64, "history_rows": 18, "history_races": 1,
                               "history_max_source_date": "20260913"}
    receipt = {"status": "completed", "stage": "capture", "started_at": "2026-10-12T06:38:58Z",
               "completed_at": "2026-10-12T06:39:09Z", "capture_directory": str(captured),
               "capture_manifest_sha256": pipeline.file_hash(captured / "artifact_manifest.json"),
               "preservation_after": {"synthetic": True}, "literal_quote_receipt": {"synthetic": True}}
    input_receipt = {"model_input_sha256": "d" * 64, "market_source_body_sha256": "e" * 64}
    probability = predictions()
    prediction = {"predictions": probability, "prediction_sha256": pipeline.digest(pipeline.body(probability)),
                  "capture_manifest_sha256": receipt["capture_manifest_sha256"], "capture_sha256": "f" * 64,
                  "protocol_sha256": pipeline.STUDY_PINS["protocol_sha256"], "input_receipt": input_receipt,
                  "serialized_model_replay_equal": True, "current_outcomes_consumed": False,
                  "completed_at": "2026-10-12T06:39:07Z"}
    selected = decision()
    selected.update(decision_created_at="2026-10-12T06:39:08Z", prediction_sha256=prediction["prediction_sha256"],
                    model_input_sha256=input_receipt["model_input_sha256"], capture_sha256=prediction["capture_sha256"],
                    market_source_body_sha256=input_receipt["market_source_body_sha256"])
    receipt.update(prediction_sha256=prediction["prediction_sha256"], decision_sha256=pipeline.digest(pipeline.body(selected)))
    (stage / "inference.json").write_bytes(pipeline.body(prediction))
    (stage / "paper-decision.json").write_bytes(pipeline.body(selected))
    checked = {"plan_directory": str(plan), "plan": {"study_directory": "synthetic-fixture-not-live",
               "paper_rule": {"primary_candidate": "market46", "stake_yen": 100}, "preservation_before": {"synthetic": True},
               "prepared_history_binding": {"state_sha256": "1" * 64, "rows": 18, "races": 1,
                   "history_date_to": "20260913", "history_parquet_sha256": pipeline.digest(b"synthetic-history")}}}
    wrapper = {"directory": str(stage), "receipt": receipt, "publication": {"published_at": "2026-10-12T06:39:10Z"}}
    monkeypatch.setattr(pipeline, "verify_seal", lambda *_args: "fixture-hash")
    monkeypatch.setattr(pipeline.prestart, "read_bundle", lambda _path: source)
    monkeypatch.setattr(pipeline, "verify_literal_quotes", lambda *_args: {"synthetic": True})
    monkeypatch.setattr(inference, "verify_manifest", lambda _path: deepcopy(pipeline.STUDY_PINS))
    monkeypatch.setattr(inference, "frame_from_capture", lambda *_args: ("synthetic-input-not-live", input_receipt))
    monkeypatch.setattr(inference, "load_models", lambda *_args: "synthetic-model-not-live")
    monkeypatch.setattr(inference, "predict_all", lambda *_args: deepcopy(probability))
    return checked, wrapper, stage


def test_capture_stage_replays_fixed_decision_and_validates_full_clockchain(monkeypatch, tmp_path):
    checked, wrapper, _stage = synthetic_capture_stage(monkeypatch, tmp_path)
    _bundle, _prediction, choice = pipeline.verify_capture_stage(checked, wrapper)
    assert choice["horse_number"] == 1


@pytest.mark.parametrize("clock", ["decision_after_post", "publication_after_post", "prediction_after_decision", "started_after_request"])
def test_capture_stage_bad_clockchain_never_passes(monkeypatch, tmp_path, clock):
    checked, wrapper, stage = synthetic_capture_stage(monkeypatch, tmp_path)
    if clock == "decision_after_post":
        path = stage / "paper-decision.json"
        choice = json.loads(path.read_bytes())
        choice["decision_created_at"] = "2026-10-12T06:45:00Z"
        path.write_bytes(pipeline.body(choice))
        wrapper["receipt"]["decision_sha256"] = pipeline.digest(pipeline.body(choice))
    if clock == "publication_after_post": wrapper["publication"]["published_at"] = "2026-10-12T06:45:00Z"
    if clock == "prediction_after_decision":
        path = stage / "inference.json"
        predicted = json.loads(path.read_bytes())
        predicted["completed_at"] = "2026-10-12T06:39:11Z"
        path.write_bytes(pipeline.body(predicted))
    if clock == "started_after_request": wrapper["receipt"]["started_at"] = "2026-10-12T06:39:01Z"
    with pytest.raises(pipeline.PipelineError, match="clockchain|started_after"):
        pipeline.verify_capture_stage(checked, wrapper)


def test_sealed_prediction_flags_cannot_hide_model_replay_failure(monkeypatch, tmp_path):
    import verify_db_shared_win_prestart_inference as inference
    checked, wrapper, _stage = synthetic_capture_stage(monkeypatch, tmp_path)
    tampered = predictions()
    tampered["market46"][0]["probability"] += .01
    monkeypatch.setattr(inference, "predict_all", lambda *_args: tampered)
    with pytest.raises(pipeline.PipelineError, match="do_not_replay"):
        pipeline.verify_capture_stage(checked, wrapper)


def synthetic_complete_chain(monkeypatch, tmp_path):
    source = card()
    predicted = {"prediction_sha256": "b" * 64, "completed_at": "2026-10-12T06:39:07Z"}
    choice = decision()
    choice["decision_created_at"] = "2026-10-12T06:39:08Z"
    html = result_html()
    observed = result_source(html)
    outcome = normalize(html, observed, choice, source)
    (tmp_path / "result.html").write_bytes(html.encode())
    (tmp_path / "result-source.json").write_bytes(pipeline.body(observed))
    (tmp_path / "settlement.json").write_bytes(pipeline.body(outcome))
    captures = {"directory": "fixture-capture", "manifest_sha256": "c" * 64,
                "publication_manifest_sha256": "d" * 64, "publication": {"published_at": "2026-10-12T06:39:10Z"},
                "receipt": {"stage": "capture", "status": "completed", "literal_quote_receipt": {"synthetic": True}}}
    settled = {"directory": str(tmp_path), "manifest_sha256": "e" * 64,
               "publication_manifest_sha256": "f" * 64, "receipt": {"stage": "settlement", "status": "completed",
               "parent_manifest_sha256": "c" * 64, "started_at": "2026-10-12T06:50:00Z",
               "completed_at": "2026-10-12T06:50:03Z", "preservation_after": {"synthetic": True},
               "settlement_sha256": pipeline.digest(pipeline.body(outcome))}}
    checked = {"plan_directory": str(tmp_path), "plan_sha256": "a" * 64,
               "plan": {"source_hashes": {}, "preservation_before": {"synthetic": True},
                        "freshness_audit": {"synthetic": True, "history_freshness_complete": False},
                        "prepared_history_binding": {"synthetic": True}}}
    monkeypatch.setattr(pipeline, "read_plan", lambda _path: checked)
    monkeypatch.setattr(pipeline, "stages", lambda _path: [captures, settled])
    monkeypatch.setattr(pipeline, "verify_capture_stage", lambda *_args: (source, predicted, choice))
    monkeypatch.setattr(pipeline, "preservation", lambda: {"synthetic": True})
    return outcome, settled


def test_full_proof_keeps_chain_completion_separate_from_profit_or_freshness_validation(monkeypatch, tmp_path):
    synthetic_complete_chain(monkeypatch, tmp_path)
    proof = pipeline._proof_payload(tmp_path)
    assert proof["phase1_pass"] and proof["actual_settlement_verified"]
    assert proof["four_candidate_training_now_eligible"]
    assert not proof["prediction_accuracy_validation_completed"]
    assert not proof["calibration_statistical_validation_completed"]
    assert not proof["roi_statistical_validation_completed"]
    assert not proof["operational_input_complete_verified"] and not proof["expected_value_authorized"]
    assert not proof["history_freshness_complete"] and not proof["purchase_authorized"]
    assert proof["actual_purchases"] == proof["skipped_planned_races"] == 0
    assert proof["planned_races"] == proof["settled_races"] == 1


@pytest.mark.parametrize("change", ["raw_body", "settlement_value", "result_price_proxy", "clock", "parent_chain", "cached_source", "model_preservation"])
def test_full_proof_independently_recomputes_actual_source_and_settlement(monkeypatch, tmp_path, change):
    outcome, settled = synthetic_complete_chain(monkeypatch, tmp_path)
    if change == "raw_body": (tmp_path / "result.html").write_bytes(result_html(payout=800).encode())
    if change == "settlement_value":
        outcome["paper_return_yen"] = 9900
        (tmp_path / "settlement.json").write_bytes(pipeline.body(outcome))
    if change == "result_price_proxy":
        outcome["settlement_from_final_odds"] = True
        (tmp_path / "settlement.json").write_bytes(pipeline.body(outcome))
    if change == "clock": settled["receipt"]["started_at"] = "2026-10-12T06:44:00Z"
    if change == "parent_chain": settled["receipt"]["parent_manifest_sha256"] = "0" * 64
    if change == "cached_source":
        path = tmp_path / "result-source.json"
        record = json.loads(path.read_bytes())
        record["source"] = "cache"
        path.write_bytes(pipeline.body(record))
    if change == "model_preservation": monkeypatch.setattr(pipeline, "preservation", lambda: {"synthetic": False})
    with pytest.raises(pipeline.PipelineError):
        pipeline._proof_payload(tmp_path)


def literal_api_fixture(tmp_path):
    bundle = card()
    payload = {"status": "middle", "data": {"yy": "26", "jyo": "08", "kai": "04", "nichi": "05",
                "rno": "11", "shusso": "18", "odds": {"1": {f"{n:02d}": ["4.9", "", str(n)] for n in range(1, 19)}}}}
    raw = pipeline.body(payload)
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources/odds.json").write_bytes(raw)
    bundle.update(quotes={str(n): 4.9 for n in range(1, 19)}, odds_status="middle")
    bundle["source_receipts"][1].update(body_file="sources/odds.json", body_sha256=pipeline.digest(raw), body_size=len(raw))
    for row in bundle["card"]["horses"]:
        row.update(odds=4.9, popularity=row["horse_number"])
    return bundle, payload


def test_literal_odds_api_body_is_reparsed_not_trusted_saved_flags(tmp_path):
    bundle, _payload = literal_api_fixture(tmp_path)
    result = pipeline.verify_literal_quotes(bundle, tmp_path)
    assert result["exact_quote_value_parity"] and result["exact_quote_runner_identity_parity"]
    assert result["exact_card_quote_popularity_parity"]
    assert "python-api/scraping/odds.py" in pipeline._SOURCE_FILES
    assert result["parser_sha256"] == pipeline.file_hash(ROOT / "python-api/scraping/odds.py")


@pytest.mark.parametrize("change", ["saved_quote", "saved_card_quote", "saved_popularity", "wrong_race", "missing_identity", "wrong_field", "yoso", "settled", "nan", "partial", "duplicate_alias", "body_tamper", "matrix_source"])
def test_literal_api_and_saved_quote_equivalence_fail_closed(tmp_path, change):
    bundle, payload = literal_api_fixture(tmp_path)
    if change == "saved_quote": bundle["quotes"]["1"] = 8.
    if change == "saved_card_quote": bundle["card"]["horses"][0]["odds"] = 8.
    if change == "saved_popularity": bundle["card"]["horses"][0]["popularity"] = 18
    if change == "wrong_race": payload["data"]["rno"] = "12"
    if change == "missing_identity": del payload["data"]["jyo"]
    if change == "wrong_field": payload["data"]["shusso"] = "17"
    if change == "yoso": payload["status"] = "yoso"
    if change == "settled": payload["status"] = "result"
    if change == "nan": payload["data"]["odds"]["1"]["01"][0] = "NaN"
    if change == "partial": del payload["data"]["odds"]["1"]["18"]
    if change == "duplicate_alias": payload["data"]["odds"]["1"]["1"] = ["4.9", "", "1"]
    if change == "matrix_source": bundle["features_extended"] = {"market_source_body_sha256": "0" * 64}
    raw = pipeline.body(payload)
    (tmp_path / "sources/odds.json").write_bytes(raw)
    if change != "body_tamper":
        bundle["source_receipts"][1].update(body_sha256=pipeline.digest(raw), body_size=len(raw))
    else:
        (tmp_path / "sources/odds.json").write_bytes(b"{}")
    with pytest.raises(pipeline.PipelineError):
        pipeline.verify_literal_quotes(bundle, tmp_path)


def prepared_binding_fixture():
    import pandas as pd
    import io
    history = pd.DataFrame({"race_id": ["202601010101", "202601010101"], "horse_number": [1, 2],
                            "race_date": ["20260913", "20260913"]})
    stream = io.BytesIO()
    history.to_parquet(stream, index=False)
    raw = stream.getvalue()
    identity = pipeline.hashlib.sha256()
    for row in history.itertuples(index=False, name=None):
        identity.update(json.dumps(row, separators=(",", ":")).encode() + b"\n")
    meta = {"raw_feature_source_hashes": {"fixed": "a" * 64}, "feature_source_hashes": {"fixed": "b" * 64},
            "history_rows": 2, "history_races": 1, "history_min_source_date": "20260913",
            "history_max_source_date": "20260913", "history_parquet_sha256": pipeline.digest(raw)}
    fresh = {"raw_feature_source_hashes": meta["raw_feature_source_hashes"], "eligible_history_rows": 2,
             "eligible_history_races": 1, "eligible_history_min_source_date": "20260913",
             "eligible_history_max_source_date": "20260913", "eligible_identity_sha256": identity.hexdigest()}
    envelope = {"state_sha256": "c" * 64, "state": {"source_rows_sha256": "d" * 64}}
    return meta, envelope, raw, fresh


def test_prepared_binding_uses_full_identity_not_just_latest_date():
    meta, envelope, raw, fresh = prepared_binding_fixture()
    result = pipeline.bind_prepared_history(meta, envelope, raw, fresh)
    assert result["identity_sha256"] == fresh["eligible_identity_sha256"]
    assert result["computed_at_registration_once"]
    assert result["rows"] == 2 and result["races"] == 1


@pytest.mark.parametrize("change", ["rows", "races", "formula", "identity", "parquet_body"])
def test_same_date_but_different_prepared_subset_or_formula_is_rejected(change):
    meta, envelope, raw, fresh = prepared_binding_fixture()
    if change == "rows": fresh["eligible_history_rows"] = 3
    if change == "races": fresh["eligible_history_races"] = 2
    if change == "formula": fresh["raw_feature_source_hashes"] = {"different": "e" * 64}
    if change == "identity": fresh["eligible_identity_sha256"] = "e" * 64
    if change == "parquet_body": meta["history_parquet_sha256"] = "e" * 64
    with pytest.raises(pipeline.PipelineError):
        pipeline.bind_prepared_history(meta, envelope, raw, fresh)


@pytest.mark.parametrize("payout_text", ["0円", "50円", "105円", "105.5円", "100.0円", "-100円", "1,00円"])
def test_actual_win_payout_rejects_invalid_units_and_fractional_tokens(payout_text):
    html = result_html().replace("490円", payout_text)
    with pytest.raises(pipeline.PipelineError):
        normalize(html)


@pytest.mark.parametrize("status", [403, 429, 500, 503, 0])
def test_http_failure_snapshot_none_is_failed_not_result_pending(monkeypatch, tmp_path, status):
    checked = {"plan_directory": str(tmp_path), "plan_sha256": "a" * 64,
               "plan": {"preservation_before": {"synthetic": True}}}
    capture_stage = {"manifest_sha256": "b" * 64, "receipt": {"stage": "capture", "status": "completed"}}
    monkeypatch.setattr(pipeline, "safe_plan", lambda path: Path(path))
    monkeypatch.setattr(pipeline, "read_plan", lambda _path: checked)
    monkeypatch.setattr(pipeline, "stages", lambda _path: [capture_stage])
    monkeypatch.setattr(pipeline, "verify_capture_stage", lambda *_args: (card(), {}, decision()))
    monkeypatch.setattr(pipeline, "preservation", lambda: {"synthetic": True})
    monkeypatch.setattr(pipeline, "now", lambda: datetime(2026, 10, 12, 7, 15, tzinfo=timezone.utc))
    html = result_html()
    observed = result_source(html)
    observed.update(status=status, body=html.encode(), html=html)
    async def response(_plan):
        return None, deepcopy(observed)
    monkeypatch.setattr(pipeline, "acquire_result", response)
    result = asyncio.run(pipeline.settle(tmp_path, allow_network=True))
    record = result["settlement_stage"]["receipt"]
    assert result["status"] == "failed"
    assert record["reason"] == f"result_http_status_{status}"
    assert record["source_http_status"] == status
    assert not (tmp_path / "proof.json").exists()


def test_real_200_verified_page_without_result_snapshot_remains_pending(monkeypatch, tmp_path):
    checked = {"plan_directory": str(tmp_path), "plan_sha256": "a" * 64,
               "plan": {"preservation_before": {"synthetic": True}}}
    capture_stage = {"manifest_sha256": "b" * 64, "receipt": {"stage": "capture", "status": "completed"}}
    monkeypatch.setattr(pipeline, "safe_plan", lambda path: Path(path))
    monkeypatch.setattr(pipeline, "read_plan", lambda _path: checked)
    monkeypatch.setattr(pipeline, "stages", lambda _path: [capture_stage])
    monkeypatch.setattr(pipeline, "verify_capture_stage", lambda *_args: (card(), {}, decision()))
    monkeypatch.setattr(pipeline, "preservation", lambda: {"synthetic": True})
    monkeypatch.setattr(pipeline, "now", lambda: datetime(2026, 10, 12, 7, 15, tzinfo=timezone.utc))
    html = result_html().replace('class="RaceTable01"', 'class="ResultsNotPublished"')
    observed = result_source(html)
    observed.update(body=html.encode(), html=html)
    async def response(_plan):
        return None, deepcopy(observed)
    monkeypatch.setattr(pipeline, "acquire_result", response)
    result = asyncio.run(pipeline.settle(tmp_path, allow_network=True))
    assert result["status"] == "pending"
    assert result["settlement_stage"]["receipt"]["reason"] == "result_or_payout_not_yet_available"


def test_access_blocked_200_is_transport_failure_not_unpublished_results():
    html = "<html>cloudflare cf-ray blocked</html>"
    with pytest.raises(pipeline.PipelineError, match="access_blocked"):
        pipeline.validate_result_response(result_source(html), html.encode())


def test_registered_prepared_binding_checks_metadata_without_reparsing_whole_history(monkeypatch, tmp_path):
    import pandas as pd
    meta, envelope, raw, fresh = prepared_binding_fixture()
    binding = pipeline.bind_prepared_history(meta, envelope, raw, fresh)
    meta.update(state_sha256=envelope["state_sha256"], history_source_rows_sha256=envelope["state"]["source_rows_sha256"])
    raw_metadata = pipeline.body(meta)
    (tmp_path / "metadata.json").write_bytes(raw_metadata)
    manifest = {"files": {"metadata.json": {"sha256": pipeline.digest(raw_metadata), "bytes": len(raw_metadata)},
                          "history.parquet": {"sha256": pipeline.digest(raw), "bytes": len(raw)}}}
    (tmp_path / "artifact_manifest.json").write_bytes(pipeline.body(manifest))
    payload = {"prepared_state": str(tmp_path), "prepared_manifest_sha256": pipeline.file_hash(tmp_path / "artifact_manifest.json"),
               "prepared_history_binding": binding, "freshness_audit": fresh}
    def no_big_read(*_args, **_kwargs):
        raise AssertionError("Registered identity must not be recomputed at cutoff")
    monkeypatch.setattr(pd, "read_parquet", no_big_read)
    assert pipeline.verify_prepared_binding(payload)
    meta["history_rows"] = 1
    (tmp_path / "metadata.json").write_bytes(pipeline.body(meta))
    with pytest.raises(pipeline.PipelineError, match="metadata_hash_changed"):
        pipeline.verify_prepared_binding(payload)
