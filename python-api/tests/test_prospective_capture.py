"""Synthetic fixtures only: no provider observation or real inference proof."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path

import pytest

from prospective.capture import (CaptureError, capture_snapshot, digest, file_digest,
    freeze_capture_protocol, load_capture_protocol, observe_http, record_capture_failure)

POST = "2026-09-20T15:00:00+09:00"
RACE = "202606040512"


def t(value):
    return datetime.fromisoformat(value)


class Clock:
    def __init__(self, value="2026-09-19T12:00:00+09:00"):
        self.value = t(value)

    def __call__(self):
        return self.value


def scorer(runners, artifact_paths):
    assert all(set(r) == {"horse_number", "horse_id", "features"} for r in runners)
    assert "model" in artifact_paths and "calibrator" in artifact_paths
    return [{"horse_number": r["horse_number"], "score": 0.5, "win_probability": 0.5} for r in runners]


def builder(card_body, artifact_paths, payload):
    # Test double only; deliberately no claim this is a production feature builder.
    assert b"Shutuba_Table" in card_body
    return {k: payload[k] for k in ("runners", "features_generated_at", "history_max_source_date")}


@pytest.fixture
def setup(tmp_path):
    store = tmp_path / "forward"
    clock = Clock()
    artifacts = {}
    for name in ("model", "calibrator", "features", "rule"):
        path = tmp_path / (name + ".txt")
        path.write_text(name, encoding="utf-8")
        artifacts[name] = path
    artifacts.update(scorer=Path(__file__), feature_builder=Path(__file__))
    protocol = {"protocol_id": "test-only", "start_at": "2026-09-20T00:00:00+09:00",
                "end_at": "2026-12-19T00:00:00+09:00", "feature_columns": ["safe_distance", "safe_prev_finish"],
                "nullable_feature_columns": ["safe_prev_finish"]}
    record = freeze_capture_protocol(store, protocol, artifacts, _clock=clock)
    return store, clock, protocol, artifacts, record


def card():
    cells = lambda n: f'<tr><td>1</td><td>{n}</td><td></td><td><a href="https://db.netkeiba.com/horse/202000000{n}/">馬</a></td><td>牡3</td><td>57</td><td>騎手</td></tr>'
    return (f'<html><title>2026年9月20日 試験レース</title><link rel="canonical" href="https://race.netkeiba.com/race/shutuba.html?race_id={RACE}">'
            '<div class="RaceData01">15:00発走 芝2000m</div><div class="RaceData02">2頭</div><table class="Shutuba_Table">' + cells(1) + cells(2) + '</table></html>').encode()


def odds(tick=None):
    return json.dumps({"status": "middle", "data": {"official_datetime": tick,
        "odds": {"1": {"1": ["3.0", "", "1"], "2": ["4.0", "", "2"]}}}}).encode()


def observe(setup, kind, body=None, **changes):
    store, clock, *_ = setup
    clock.value = t("2026-09-20T14:54:35+09:00")
    async def fetcher(url):
        clock.value += timedelta(seconds=1)
        return {"status": 200, "body": body if body is not None else odds() if kind == "odds" else card(),
                "url": url, "source": "network", **changes}
    return asyncio.run(observe_http(store, RACE, POST, kind, fetcher=fetcher, _clock=clock))


def payload(setup):
    store, clock, _, _, record = setup
    c = observe(setup, "race_card"); o = observe(setup, "odds")
    clock.value = t("2026-09-20T14:54:40+09:00")
    return {"race_id": RACE, "post_time": POST, "card_receipt_sha256": c["receipt_sha256"],
            "odds_receipt_sha256": o["receipt_sha256"], "features_generated_at": "2026-09-20T14:54:37+09:00",
            "history_max_source_date": "20260919", "artifact_hashes": record["protocol"]["artifact_hashes"],
            "runners": [{"horse_number": n, "horse_id": f"202000000{n}",
                         "features": {"safe_distance": 2000, "safe_prev_finish": None}} for n in (1, 2)]}


def test_unverified_capture_never_policy_ready(setup):
    store, clock, *_ = setup
    data = payload(setup)
    result = capture_snapshot(store, data, _clock=clock)
    assert result["snapshot_sha256"] == digest(result["snapshot"])
    snap = result["snapshot"]
    assert not snap["policy_ready"] and not snap["inference_verified"] and not snap["raw_features_recomputed"]
    assert snap["prediction_at"] is None and snap["runners"][0]["odds"] == 3
    assert "odds" not in data["runners"][0]  # Caller data unchanged.
    assert snap["automatic_purchase"] is False


def test_verified_callbacks_and_exact_idempotent_retry(setup):
    store, clock, *_ = setup; data = payload(setup)
    a = capture_snapshot(store, data, feature_builder=builder, score_and_calibrate=scorer, _clock=clock)
    b = capture_snapshot(store, data, feature_builder=builder, score_and_calibrate=scorer, _clock=clock)
    assert a == b and a["snapshot"]["policy_ready"]
    assert sum(r["win_probability"] for r in a["snapshot"]["runners"]) == 1


def test_payload_scores_alone_do_not_verify_inference(setup):
    store, clock, *_ = setup; data = payload(setup)
    for r in data["runners"]:r.update(score=1, win_probability=0.5)
    snap = capture_snapshot(store, data, _clock=clock)["snapshot"]
    assert snap["prediction_at"] and not snap["policy_ready"] and not snap["inference_verified"]


@pytest.mark.parametrize("mutation,reason", [
    (lambda p:p.update(finish=1), "payload-fields"),
    (lambda p:p["runners"][0]["features"].update(finish=1), "allowlist"),
    (lambda p:p["runners"][0]["features"].pop("safe_prev_finish"), "allowlist"),
    (lambda p:p["runners"][0]["features"].update(safe_distance=None), "required-feature"),
    (lambda p:p["runners"][0].update(horse_id="wrong"), "runner-binding"),
    (lambda p:p["runners"].pop(), "complete-feature"),
    (lambda p:p.update(history_max_source_date="20260920"), "same-day-or-future"),
    (lambda p:p.update(history_max_source_date="20260921"), "same-day-or-future"),
    (lambda p:p.update(features_generated_at="2026-09-20T14:54:00+09:00"), "features-source-time"),
    (lambda p:p.update(features_generated_at="2026-09-20T14:54:38"), "timezone"),
    (lambda p:p["artifact_hashes"].update(model="0"*64), "artifact-binding"),
    (lambda p:p.update(post_time="2026-09-20T15:10:00+09:00"), "window"),
])
def test_invalid_capture_fails_closed(setup, mutation, reason):
    store, clock, *_ = setup; data = payload(setup); mutation(data)
    with pytest.raises(CaptureError, match=reason):capture_snapshot(store, data, _clock=clock)
    assert not (store / "captures").exists()


@pytest.mark.parametrize("when,reason", [("2026-09-20T14:54:29+09:00", "before-fixed"),
    ("2026-09-20T14:55:00.001+09:00", "outside-cutoff")])
def test_capture_deadline_window(setup, when, reason):
    store, clock, *_ = setup; data = payload(setup); clock.value = t(when)
    with pytest.raises(CaptureError, match=reason):capture_snapshot(store, data, _clock=clock)


@pytest.mark.parametrize("change", [{"source":"cache"},{"source":"resume-cache"},{"status":403},{"status":429},
    {"url":"https://race.netkeiba.com/race/result.html?race_id="+RACE}])
def test_network_evidence_required(setup, change):
    with pytest.raises(CaptureError):observe(setup, "odds", **change)
    assert not (setup[0] / "receipts").exists()


@pytest.mark.parametrize("tick", ["2026-09-20T14:52:59+09:00", "2026-09-20T14:54:37+09:00", "2026-09-20T14:54:30", "old"])
def test_provider_tick_not_replaced_with_now(setup, tick):
    with pytest.raises(CaptureError):observe(setup, "odds", odds(tick))


def test_provider_optional_direct_receipt_kept_separate(setup):
    receipt = observe(setup,"odds")
    assert receipt["provider_time"] is None
    assert receipt["source_at"] == receipt["received_at"]
    assert receipt["request_started_at"] < receipt["received_at"]


@pytest.mark.parametrize("status", ["result", "yoso", "final"])
def test_reject_non_market_or_final_odds(setup, status):
    body=json.loads(odds());body["status"]=status
    with pytest.raises(CaptureError):observe(setup, "odds", json.dumps(body).encode())


@pytest.mark.parametrize("old,new", [(b"2026",b"2025"),(b"15:00",b"15:10"),
    (b"Shutuba_Table",b"RaceTable01"),("芝".encode(),"障".encode()),
    (b"2020000001",b"2020000002")])
def test_strict_card_identity_and_schedule(setup, old, new):
    with pytest.raises(CaptureError):observe(setup,"race_card",card().replace(old,new))


def test_incomplete_odds_field_not_promoted(setup):
    store, clock, *_=setup; data=payload(setup)
    body=json.loads(odds());body["data"]["odds"]["1"]["3"]=["8.0"]
    receipt=observe(setup,"odds",json.dumps(body).encode());data["odds_receipt_sha256"]=receipt["receipt_sha256"]
    clock.value=t("2026-09-20T14:54:40+09:00")
    with pytest.raises(CaptureError,match="incomplete-card-odds"):capture_snapshot(store,data,_clock=clock)


def test_raw_or_protocol_tamper_detected(setup):
    store, clock, *_=setup; data=payload(setup)
    receipt=json.loads((store/'receipts'/(data['odds_receipt_sha256']+'.json')).read_text())
    (store/'objects'/receipt['body_sha256']).write_bytes(b'changed')
    with pytest.raises(CaptureError,match="object-hash"):capture_snapshot(store,data,_clock=clock)


def test_artifact_tamper_detected(setup):
    store, clock, _, _, record=setup
    (store/'objects'/record['protocol']['artifact_hashes']['model']).write_bytes(b'changed')
    with pytest.raises(CaptureError,match="object-hash"):load_capture_protocol(store)


def test_cannot_refreeze_or_overwrite_race(setup):
    store, clock, protocol, artifacts, _=setup
    with pytest.raises(CaptureError,match="already-frozen"):freeze_capture_protocol(store,protocol,artifacts,_clock=clock)
    data=payload(setup);capture_snapshot(store,data,_clock=clock)
    data['runners'][0]['features']['safe_distance']=1800
    with pytest.raises(CaptureError,match="immutable-record-conflict"):capture_snapshot(store,data,_clock=clock)


def test_unbound_callback_rejected(setup, monkeypatch):
    store,clock,*_=setup;data=payload(setup)
    monkeypatch.setattr('prospective.capture.inspect.getsourcefile',lambda f:None)
    with pytest.raises(CaptureError,match="source-not-frozen"):capture_snapshot(store,data,score_and_calibrate=scorer,_clock=clock)


def test_inference_must_finish_before_cutoff(setup):
    store,clock,*_=setup;data=payload(setup)
    def late(rows, paths):
        clock.value=t('2026-09-20T14:55:01+09:00')
        return scorer(rows,paths)
    with pytest.raises(CaptureError,match="outside-cutoff"):capture_snapshot(store,data,score_and_calibrate=late,_clock=clock)


def test_failures_are_append_only_not_valid_captures(setup):
    store,clock,*_=setup
    a=record_capture_failure(store,RACE,'source-unavailable',_clock=clock)
    record_capture_failure(store,RACE,'source-unavailable',_clock=clock)
    assert a['paper_action']=='skip' and len(list((store/'failures').glob('*.json')))==2
    assert not (store/'captures').exists()


@pytest.mark.parametrize("key,value", [('feature_columns',['safe_finish']),('feature_columns',['safe_odds']),
    ('cutoff_seconds',0),('max_source_age_seconds',121),('start_at','2026-09-18T00:00:00+09:00')])
def test_invalid_protocol_rejected(setup,tmp_path,key,value):
    _,clock,protocol,artifacts,_=setup;protocol[key]=value
    with pytest.raises(CaptureError):freeze_capture_protocol(tmp_path/'other',protocol,artifacts,_clock=clock)


def test_cli_refuses_implicit_network(tmp_path):
    path=Path(__file__).resolve().parents[2]/'scripts/capture_forward_snapshot.py'
    spec=importlib.util.spec_from_file_location('capture_cli_test',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with pytest.raises(SystemExit) as err:module.main(['--store',str(tmp_path),'observe','--race-id',RACE,'--post-time',POST,'--kind','odds'])
    assert err.value.code==2


@pytest.mark.parametrize('post',['2026-09-20T15:00:59+09:00','2026-09-20T15:00:00.001+09:00'])
def test_subminute_post_cannot_extend_cutoff(setup,post):
    store,clock,*_=setup;data=payload(setup);data['post_time']=post
    with pytest.raises(CaptureError,match='minute-resolution'):capture_snapshot(store,data,_clock=clock)


@pytest.mark.parametrize('replacement',['3頭',''])
def test_partial_card_not_complete_even_with_matching_odds(setup,replacement):
    with pytest.raises(CaptureError):observe(setup,'race_card',card().replace('2頭'.encode(),replacement.encode()))


def test_post_itself_must_be_in_protocol_period(setup):
    store,clock,*_=setup;data=payload(setup);data['post_time']='2026-12-19T00:04:00+09:00'
    clock.value=t('2026-12-18T23:58:40+09:00')
    with pytest.raises(CaptureError,match='race-outside'):capture_snapshot(store,data,_clock=clock)
