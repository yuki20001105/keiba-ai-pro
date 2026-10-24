"""Isolated capture gates, immutable save/reload, actual-parser integration."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from research_capture import prestart as capture

ROOT = Path(__file__).resolve().parents[2]
RACE = "202608040511"
DAY = "20261012"
NOW = datetime(2026, 10, 12, 6, 40, 2, tzinfo=timezone.utc)
POST = datetime(2026, 10, 12, 6, 45, tzinfo=timezone.utc)


def snapshot():
    return {"race_info": {"race_id": RACE, "date": DAY, "post_time": "15:45", "distance": 1400,
                          "num_horses": 2, "venue": "京都", "track_type": "芝", "race_class": "G2",
                          "lap_cumulative": [], "lap_sectional": [], "odds_status": "middle"},
            "horses": [{"race_id": RACE, "horse_number": i, "horse_id": f"202310000{i}",
                        "horse_name": f"Horse {i}", "age": 3, "sex": "牡", "jockey_weight": 57,
                        "bracket_number": i, "weight_kg": 480, "weight_diff": 2,
                        "date": DAY, "distance": 1400, "venue": "京都", "track_type": "芝",
                        "_shutuba": True, "finish_position": None, "finish_time": None,
                        "odds": 9.9, "odds_status": "middle"} for i in (1, 2)], "return_tables": []}


def sources():
    return [{"url": capture._expected_urls(RACE)[kind], "request_url": capture._expected_urls(RACE)[kind],
             "status": 200, "source": "network", "request_started_at": "2026-10-12T06:39:59Z",
             "observed_at": "2026-10-12T06:40:01Z", "body": raw}
            for kind, raw in (("card", b"<html>real card</html>"),
                              ("odds", json.dumps({"status": "middle", "data": {"odds": {"1": {
                                  "01": ["2.5", "", "1"], "02": ["3.7", "", "2"]}}}}).encode()))]


def features(_snapshot, _db_path):
    from training.db_shared_optimized import FEATURE_COLUMNS
    return {"columns": list(FEATURE_COLUMNS), "values": [[None] * 46 for _ in _snapshot["horses"]],
            "profile": "fixture", "input_sha256": "f" * 64}, {
                "current_outcomes_consumed": False, "same_day_or_future_history_used": False,
                "history_max_source_date": "20261011"}


def build(monkeypatch, *, card=None, inputs=None, mode="live", policy=None, feature_builder=features):
    monkeypatch.setattr(capture, "utcnow", lambda: NOW)
    return capture.make_bundle(snapshot=card or snapshot(), sources=inputs or sources(), race_id=RACE,
                               post_time=POST, db_path=Path("unused.db"), mode=mode,
                               policy=policy, feature_builder=feature_builder)


def test_exact_quote_body_overwrites_display_quote_without_ev_authority(monkeypatch):
    bundle, files = build(monkeypatch)
    assert bundle["capture_validation"]["point_in_time_verified"]
    assert bundle["quotes"] == {"1": 2.5, "2": 3.7}
    assert [row["odds"] for row in bundle["card"]["horses"]] == [2.5, 3.7]
    assert set(files) == {"sources/card.html", "sources/odds.json"}
    assert len(bundle["features46"]["columns"]) == 46
    assert not bundle["eligible_for_ev"] and not bundle["purchase_approved"]
    assert not bundle["model_activated"] and not bundle["forward_modified"]
    assert not bundle["capture_validation"]["simultaneous"]


@pytest.mark.parametrize("field,value", [("finish_position", 1), ("finish", 2),
                                         ("finish_time", "1:20.3"), ("time_seconds", 80.3),
                                         ("last_3f_time", 35), ("margin", "0.2"),
                                         ("speed_deviation", 0.2), ("odds_status", "result")])
def test_current_results_never_form_inputs(monkeypatch, field, value):
    card = snapshot()
    card["horses"][0][field] = value
    with pytest.raises(capture.CaptureError, match="result"):
        build(monkeypatch, card=card)


@pytest.mark.parametrize("change", ["duplicate_number", "duplicate_id", "missing_id", "partial",
                                    "wrong_race", "wrong_distance", "lifecycle", "cancelled", "payout", "lap"])
def test_entry_identity_completeness_and_lifecycle_fail_closed(monkeypatch, change):
    card = snapshot()
    if change == "duplicate_number": card["horses"][1]["horse_number"] = 1
    if change == "duplicate_id": card["horses"][1]["horse_id"] = card["horses"][0]["horse_id"]
    if change == "missing_id": card["horses"][0]["horse_id"] = ""
    if change == "partial": card["race_info"]["num_horses"] = 3
    if change == "wrong_race": card["horses"][0]["race_id"] = "202608040512"
    if change == "wrong_distance": card["horses"][0]["distance"] = 1600
    if change == "lifecycle": card["horses"][0]["_shutuba"] = False
    if change == "cancelled": card["horses"][0]["entry_status"] = "取消"
    if change == "payout": card["return_tables"] = [{"payout": 100}]
    if change == "lap": card["race_info"]["lap_cumulative"] = [10, 20]
    with pytest.raises(capture.CaptureError):
        build(monkeypatch, card=card)


@pytest.mark.parametrize("source", ["cache", "resume-cache", "dedup", "sealed-replay", "network-error"])
def test_cache_and_replay_never_gain_fresh_network_authority(monkeypatch, source):
    inputs = sources()
    inputs[0]["source"] = source
    bundle, _ = build(monkeypatch, inputs=inputs)
    assert not bundle["capture_validation"]["point_in_time_verified"]
    assert "card_fresh_network_not_verified" in bundle["capture_validation"]["reason_codes"]


@pytest.mark.parametrize("status", ["yoso", "result", "settled", "unknown"])
def test_only_actual_prestart_middle_odds_allowed(monkeypatch, status):
    inputs = sources()
    value = json.loads(inputs[1]["body"])
    value["status"] = status
    inputs[1]["body"] = json.dumps(value).encode()
    bundle, _ = build(monkeypatch, inputs=inputs)
    assert not bundle["capture_validation"]["point_in_time_verified"]
    assert "actual_prestart_odds_status_required" in bundle["capture_validation"]["reason_codes"]


@pytest.mark.parametrize("price", [0, -1, 0.9, "NaN", "Infinity", None, "---"])
def test_invalid_quotes_are_not_silently_imputed(monkeypatch, price):
    inputs = sources()
    value = json.loads(inputs[1]["body"])
    value["data"]["odds"]["1"]["01"][0] = price
    inputs[1]["body"] = json.dumps(value).encode()
    bundle, _ = build(monkeypatch, inputs=inputs)
    assert not bundle["capture_validation"]["point_in_time_verified"]
    # Invalid non-finite prices are excluded from final quote payload, not
    # encoded as a fake finite odds value / zero.
    assert bundle["capture_validation"]["reason_codes"]


@pytest.mark.parametrize("change,reason", [
    ("future", "card_source_clock_invalid"), ("stale", "card_source_stale"),
    ("backwards", "card_source_clock_invalid"), ("missing_clock", "card_source_clock_unverified"),
    ("naive_clock", "card_source_clock_unverified"), ("outside_window", "card_source_outside_fixed_window"),
    ("skew", "card_quote_skew_exceeded"), ("wrong_url", "source_identity_or_count_invalid"),
    ("wrong_request", "card_request_identity_invalid"), ("missing_body", "card_source_body_missing"),
    ("http_error", "card_fresh_network_not_verified"), ("duplicate_source", "source_identity_or_count_invalid"),
])
def test_source_clock_identity_and_fixed_window_gates(monkeypatch, change, reason):
    inputs = sources()
    if change == "future": inputs[0]["observed_at"] = "2026-10-12T06:40:03Z"
    if change == "stale": inputs[0]["observed_at"] = "2026-10-12T06:30:00Z"
    if change == "backwards": inputs[0]["request_started_at"] = "2026-10-12T06:40:02Z"
    if change == "missing_clock": inputs[0].pop("observed_at")
    if change == "naive_clock": inputs[0]["observed_at"] = "2026-10-12T06:40:01"
    if change == "outside_window": inputs[0]["observed_at"] = "2026-10-12T06:38:00Z"
    if change == "skew": inputs[0]["observed_at"] = "2026-10-12T06:39:00Z"
    if change == "wrong_url": inputs[0]["url"] = inputs[0]["url"].replace(RACE, "202608040512")
    if change == "wrong_request": inputs[0]["request_url"] = inputs[0]["request_url"].replace(RACE, "202608040512")
    if change == "missing_body": inputs[0]["body"] = b""
    if change == "http_error": inputs[0]["status"] = 403
    if change == "duplicate_source": inputs.append(deepcopy(inputs[0]))
    bundle, _ = build(monkeypatch, inputs=inputs)
    assert not bundle["capture_validation"]["point_in_time_verified"]
    assert reason in bundle["capture_validation"]["reason_codes"]


def test_source_clock_is_rechecked_after_slow_feature_generation(monkeypatch):
    monkeypatch.setattr(capture, "utcnow", lambda: NOW)

    def slow(card, db):
        result = features(card, db)
        monkeypatch.setattr(capture, "utcnow", lambda: POST)
        return result

    bundle, _ = capture.make_bundle(snapshot=snapshot(), sources=sources(), race_id=RACE, post_time=POST,
                                   db_path=Path("unused.db"), mode="live", feature_builder=slow)
    assert not bundle["capture_validation"]["point_in_time_verified"]
    assert "feature_generation_finished_after_deadline" in bundle["capture_validation"]["reason_codes"]


def test_post_time_and_date_must_match_independent_card(monkeypatch):
    card = snapshot()
    card["race_info"]["post_time"] = "15:40"
    with pytest.raises(capture.CaptureError, match="post_time_mismatch"):
        build(monkeypatch, card=card)


def test_profile_outcome_fields_are_not_persisted_as_input(monkeypatch):
    card = snapshot()
    card["horses"][0].update(prev_finish=1, total_prize_money=10000000, grade="A", profile_current_rating=500)
    bundle, _ = build(monkeypatch, card=card)
    assert not {"prev_finish", "total_prize_money", "grade", "profile_current_rating"}.intersection(bundle["card"]["horses"][0])


def test_replay_keeps_source_timestamps_but_never_claims_live(monkeypatch):
    bundle, _ = build(monkeypatch, mode="sealed_replay")
    assert not bundle["capture_validation"]["point_in_time_verified"]
    assert "replay_is_not_live_capture" in bundle["capture_validation"]["reason_codes"]
    assert bundle["captured_at"] == "2026-10-12T06:40:02Z"
    assert bundle["source_receipts"][0]["observed_at"] == sources()[0]["observed_at"]


def test_two_attempts_are_distinct_immutable_records(monkeypatch, tmp_path):
    bundle, files = build(monkeypatch)
    first = capture.persist_bundle(tmp_path / "research", bundle, files)
    second = capture.persist_bundle(tmp_path / "research", bundle, files)
    assert first["path"] != second["path"]
    assert first["capture_sha256"] == second["capture_sha256"]
    assert capture.read_bundle(Path(first["path"])) == bundle
    assert len(list((tmp_path / "research/captures").iterdir())) == 2


@pytest.mark.parametrize("file", ["bundle.json", "sources/card.html", "sources/odds.json"])
def test_readback_detects_any_payload_mutation(monkeypatch, tmp_path, file):
    bundle, files = build(monkeypatch)
    receipt = capture.persist_bundle(tmp_path / "research", bundle, files)
    directory = Path(receipt["path"])
    with (directory / file).open("ab") as stream: stream.write(b"x")
    with pytest.raises(capture.CaptureError, match="hash_mismatch"):
        capture.read_bundle(directory)


def test_partial_record_is_never_read_as_completed(monkeypatch, tmp_path):
    directory = tmp_path / "x.partial"
    directory.mkdir()
    with pytest.raises(capture.CaptureError, match="incomplete"):
        capture.read_bundle(directory)


@pytest.mark.parametrize("store", [ROOT, ROOT / "keiba/data", ROOT / "python-api/data",
                                   ROOT / "python-api/models", ROOT / "reports/generated/forward-runtime/h04-live-20260920-v2"])
def test_research_cannot_write_existing_runtime_stores(store):
    with pytest.raises(capture.CaptureError):
        capture._safe_store(store)


@pytest.mark.parametrize("kwargs", [{"lead_seconds": 0}, {"lead_seconds": 60, "tolerance_seconds": 60},
                                   {"max_source_age_seconds": -1}, {"tolerance_seconds": True}])
def test_invalid_policy_never_waives_clock_gates(kwargs):
    with pytest.raises(capture.CaptureError):
        capture.CapturePolicy(**kwargs)


def test_failure_attempt_is_recorded_in_denominator_without_success(tmp_path):
    receipt = capture.record_failure(tmp_path / "research", RACE, "api_unavailable", post_time=POST)
    bundle = capture.read_bundle(Path(receipt["path"]))
    assert bundle["mode"] == "failed_attempt"
    assert bundle["capture_validation"]["reason_codes"] == ["api_unavailable"]
    assert not bundle["capture_validation"]["point_in_time_verified"]


CARD = """<html><head><title>Entry card | 2026年10月12日</title>
<link rel="canonical" href="https://race.netkeiba.com/race/shutuba.html?race_id=202608040511">
</head><body><h1 class="RaceName">Test race</h1><div class="RaceData01">15:45発走 / 芝1400m (右)</div>
<div class="RaceData02">4回 京都 5日目 G2</div><table class="Shutuba_Table">""" + "".join(
    f'<tr><td>{i}</td><td>{i}</td><td></td><td><a href="/horse/202310000{i}/">Horse {i}</a></td>'
    '<td>牡3</td><td>57.0</td><td><a href="/jockey/result/recent/00001/">Jockey</a></td>'
    '<td><a href="/trainer/result/recent/00001/">Trainer</a></td><td>480(+2)</td><td>---</td><td>**</td></tr>'
    for i in (1, 2)) + "</table></body></html>"


def test_existing_parser_current_route_observer_restored_and_profile_not_fetched(monkeypatch):
    from scraping import race, odds
    from scraping.fetch_pipeline import FetchResult
    calls = []

    async def fake_fetch(_session, url, **kwargs):
        calls.append((url, kwargs))
        raw = CARD.encode() if "shutuba.html" in url else sources()[1]["body"]
        return FetchResult(url, url, 200, raw, "network", 1), raw.decode()

    async def forbidden_profile(*_args, **_kwargs):
        raise AssertionError("latest horse profile must not be fetched")

    monkeypatch.setattr(race, "fetch_text", fake_fetch)
    monkeypatch.setattr(odds, "fetch_text", fake_fetch)
    monkeypatch.setattr(race, "scrape_horse_detail", forbidden_profile)
    card, inputs = asyncio.run(capture.acquire_current_card(None, RACE, DAY, allow_network=True, clock=lambda: NOW))
    assert len(calls) == 2 and len(inputs) == 2
    assert calls[0][1]["force_refresh"] is True
    assert calls[1][1]["use_cache"] is False
    assert calls[1][1]["force_refresh"] is True
    assert len(card["horses"]) == 2
    assert race.fetch_text is fake_fetch and odds.fetch_text is fake_fetch
    assert race.scrape_horse_detail is forbidden_profile


@pytest.mark.parametrize("allow,day", [(False, DAY), (True, "20261011"), (True, "20261013")])
def test_network_authority_and_current_day_checked_before_any_fetch(allow, day):
    with pytest.raises(capture.CaptureError):
        asyncio.run(capture.acquire_current_card(None, RACE, day, allow_network=allow, clock=lambda: NOW))


def test_live_entry_observer_restores_originals_even_if_provider_blocks(monkeypatch):
    from scraping import race, odds
    from scraping.fetch_pipeline import FetchAccessBlocked

    async def blocked(*_args, **_kwargs):
        raise FetchAccessBlocked("http-403")

    monkeypatch.setattr(race, "fetch_text", blocked)
    original_odds = odds.fetch_text
    with pytest.raises(FetchAccessBlocked):
        asyncio.run(capture.acquire_current_card(None, RACE, DAY, allow_network=True, clock=lambda: NOW))
    assert race.fetch_text is blocked and odds.fetch_text is original_odds
    assert not capture._CAPTURE_LOCK.locked()


def test_sealed_actual_html_quote_pair_through_real_parser_save_reload(monkeypatch, tmp_path):
    store = ROOT / "reports/generated/forward-runtime/h04-live-20260920-v2"
    card = store / "receipts/910f4f9c386971466df92c720aa650f8aa5ca1b1d24297d6b25f32def482b9ee.json"
    odds = store / "receipts/0075b494ba281cf0fa75d92b3ccabed4fb247569a1bda3e36906b02ed24ab26a.json"
    if not card.exists() or not odds.exists():
        pytest.skip("sealed local actual-page evidence not distributed in repository")
    before = {path: capture.sha256(path.read_bytes()) for path in (card, odds)}
    parsed, inputs = asyncio.run(capture.replay_sealed_sources(card, odds, store / "objects", "202608040405", "20261011"))
    assert len(parsed["horses"]) == 11 and parsed["race_info"]["post_time"] == "12:10"
    bundle, files = capture.make_bundle(snapshot=parsed, sources=inputs, race_id="202608040405",
                                       post_time=datetime(2026, 10, 11, 3, 10, tzinfo=timezone.utc),
                                       db_path=Path("unused.db"), mode="sealed_replay", feature_builder=features)
    receipt = capture.persist_bundle(tmp_path / "research", bundle, files)
    reloaded = capture.read_bundle(Path(receipt["path"]))
    assert reloaded["odds_status"] == "middle" and len(reloaded["quotes"]) == 11
    assert not reloaded["capture_validation"]["point_in_time_verified"]
    assert before == {path: capture.sha256(path.read_bytes()) for path in before}


def test_full_db_history_includes_competitors_for_future_entity_extensions(tmp_path):
    from experiments import dataset as canonical
    from training import db_shared_features as shared
    from training import db_shared_optimized as optimized
    path = tmp_path / "fixture.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE races_ultimate (race_id TEXT PRIMARY KEY, data TEXT)")
    db.execute("CREATE TABLE race_results_ultimate (race_id TEXT, data TEXT)")
    for index, day in enumerate(("20261010", "20261011")):
        rid = f"20260804{index + 3:02d}01"
        info = {"race_id": rid, "date": day, "distance": 1400, "num_horses": 4,
                "venue": "京都", "track_type": "芝", "race_class": "未勝利", "field_condition": "良"}
        db.execute("INSERT INTO races_ultimate VALUES (?,?)", (rid, json.dumps(info)))
        for number in range(1, 5):
            horse = {**info, "horse_id": f"202310000{number}", "horse_number": number,
                     "finish_position": number, "finish_time": 80 + number,
                     "odds": 2.5 + number, "popularity": number, "sex": "牡", "age": 3,
                     "jockey_weight": 57, "weight_kg": 480, "weight_diff": 2}
            db.execute("INSERT INTO race_results_ultimate VALUES (?,?)", (rid, json.dumps(horse)))
    db.commit()
    db.close()
    before = capture.sha256(path.read_bytes())
    payload, audit = capture.raw46_features(snapshot(), path)
    assert len(payload["columns"]) == 46 and audit["history_rows"] == 8
    assert payload["canonical_source_snapshot"]["history_rows"] == 8
    import io
    history = pd.read_parquet(io.BytesIO(payload["_canonical_history_parquet"]))
    assert history.horse_id.nunique() == 4  # card has only two horses
    assert history.race_date.max() == "20261011"
    assert capture.sha256(path.read_bytes()) == before
    assert not path.with_suffix(".db-wal").exists()
    current = shared._current_card(snapshot()["race_info"], snapshot()["horses"])
    expected = optimized.select_features(shared.build_training_features(current, history))
    actual = pd.DataFrame(payload["values"], columns=payload["columns"])
    for column in actual:
        if expected[column].dtype.kind in "fi":
            np.testing.assert_allclose(pd.to_numeric(actual[column]), expected[column], equal_nan=True)
        else:
            assert actual[column].tolist() == [None if pd.isna(value) else value for value in expected[column]]


def _cli():
    path = ROOT / "scripts/capture_db_shared_win_prestart.py"
    spec = importlib.util.spec_from_file_location("capture_cli_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_missing_network_authority_records_failure_without_fetch(tmp_path, monkeypatch):
    cli = _cli()

    async def forbidden(*args, **kwargs):
        raise AssertionError("network must not be called")

    monkeypatch.setattr(cli, "acquire_current_card", forbidden)
    assert cli.main(["capture", "--race-id", RACE, "--race-date", DAY, "--post-time", POST.isoformat(),
                     "--store", str(tmp_path / "research")]) == 2
    attempts = list((tmp_path / "research/captures").iterdir())
    assert len(attempts) == 1
    assert capture.read_bundle(attempts[0])["mode"] == "failed_attempt"


def extended_payload(monkeypatch):
    import io
    from test_db_shared_win_direct_features import full_history
    from training import db_shared_features as shared
    from training import db_shared_optimized as optimized
    current = shared._current_card(snapshot()["race_info"], snapshot()["horses"])
    history = full_history()
    raw47 = shared.build_training_features(current, history)
    raw46 = optimized.select_features(raw47)
    buffer = io.BytesIO()
    history.to_parquet(buffer, index=False)
    history_bytes = buffer.getvalue()
    source = {"target_race_id": RACE, "target_date": DAY, "canonical_columns": list(current),
              "current": capture._records(current), "history_object": "sources/history.parquet",
              "history_object_sha256": capture.sha256(history_bytes), "history_rows": len(history),
              "raw47_columns": list(raw47), "raw47_values": capture._matrix_values(raw47),
              "history_scope": "ALL complete eligible JRA flat rows since 2013; strictly prior target day"}

    def frozen_builder(_snapshot, _path):
        return {"columns": list(raw46), "values": capture._matrix_values(raw46),
                "canonical_source_snapshot": deepcopy(source),
                "canonical_source_snapshot_sha256": capture.sha256(capture._body(source)),
                "_canonical_history_parquet": history_bytes}, {
                    "current_outcomes_consumed": False, "same_day_or_future_history_used": False,
                    "history_max_source_date": str(history.race_date.max())}

    return build(monkeypatch, feature_builder=frozen_builder)


def test_frozen_full_history_extension_88_plus4_quotes_exact_base46_save_reload(monkeypatch, tmp_path):
    bundle, objects = extended_payload(monkeypatch)
    enhanced = capture.attach_direct_features(bundle, objects)
    extended = enhanced["features_extended"]
    assert extended["feature_count"] == len(extended["columns"]) == 92
    assert len(extended["values"]) == 2
    receipt = extended["formula_receipt"]
    assert receipt["base46_parity"]["verified"]
    assert not receipt["input_outcomes_consumed"] and not receipt["dummy_current_results_used"]
    assert receipt["history_rows"] == 72
    assert receipt["history_date_to"] < DAY
    values = pd.DataFrame(extended["values"], columns=extended["columns"])
    assert values.normalized_market_probability.sum() == pytest.approx(1)
    np.testing.assert_allclose(values.log_odds, np.log([2.5, 3.7]))
    assert values.market_rank.tolist() == [1, 2]
    assert values.field_size.tolist() == [2, 2]
    assert enhanced["extension_contract"]["status"] == "train_current_formula_and_exact_base46_parity_verified"
    assert not enhanced["eligible_for_ev"] and not enhanced["purchase_approved"]
    saved = capture.persist_bundle(tmp_path / "research", enhanced, objects)
    assert capture.read_bundle(Path(saved["path"]))["features_extended"] == extended


@pytest.mark.parametrize("change", ["missing", "scope", "hash", "future", "parity", "price_keys"])
def test_extended_research_never_waives_full_history_and_parity(monkeypatch, change):
    import io
    bundle, objects = extended_payload(monkeypatch)
    if change == "missing":
        objects.pop("sources/history.parquet")
    elif change == "price_keys":
        bundle["quotes"].pop("1")
    else:
        source = json.loads(objects["sources/canonical_inputs.json"])
        if change == "scope": source["history_scope"] = "current horses only"
        if change == "hash": source["history_object_sha256"] = "0" * 64
        if change == "parity": source["raw47_values"][0][source["raw47_columns"].index("safe_distance")] = 2000
        if change == "future":
            history = pd.read_parquet(io.BytesIO(objects["sources/history.parquet"]))
            history.loc[history.race_date.str.startswith("2026"), "race_date"] = DAY
            buffer = io.BytesIO()
            history.to_parquet(buffer, index=False)
            objects["sources/history.parquet"] = buffer.getvalue()
            source["history_object_sha256"] = capture.sha256(objects["sources/history.parquet"])
        objects["sources/canonical_inputs.json"] = capture._body(source)
    with pytest.raises((capture.CaptureError, ValueError)):
        capture.attach_direct_features(bundle, objects)


def test_extended_capture_uses_sealed_rows_not_a_second_db_or_http_read(monkeypatch):
    bundle, objects = extended_payload(monkeypatch)
    import sqlite3
    from scraping import race, odds

    def forbidden(*args, **kwargs):
        raise AssertionError("no current DB or HTTP call allowed in extension replay")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(race, "fetch_text", forbidden)
    monkeypatch.setattr(odds, "fetch_text", forbidden)
    result = capture.attach_direct_features(bundle, objects)
    assert result["features_extended"]["feature_count"] == 92


@pytest.mark.parametrize("provider_date,reason", [
    ("Mon, 12 Oct 2026 06:00:00 GMT", "card_provider_local_clock_skew_exceeded"),
    ("not-a-date", "card_provider_clock_invalid")])
def test_provider_http_date_local_clock_mismatch_is_recorded(monkeypatch, provider_date, reason):
    inputs = sources()
    inputs[0]["server_date"] = provider_date
    bundle, _ = build(monkeypatch, inputs=inputs)
    assert reason in bundle["capture_validation"]["reason_codes"]
    assert not bundle["capture_validation"]["point_in_time_verified"]


def prepared_fixture(monkeypatch, tmp_path):
    from test_db_shared_win_direct_features import full_history
    from training import db_shared_features as shared
    history = full_history()
    monkeypatch.setattr(shared, "load_db_frames", lambda _db, lower, upper:
                        (history.copy(), history.iloc[:0].copy(), {"date_from": lower, "date_to": upper}))
    monkeypatch.setattr(capture, "utcnow", lambda: NOW)
    receipt = capture.prepare_history_state(Path("unused.db"), DAY, tmp_path / "research")
    return Path(receipt["directory"]), history


def test_prepared_fast46_and88_market_inputs_equal_cold_exactly(monkeypatch, tmp_path):
    from training import db_shared_features as shared
    prepared, history = prepared_fixture(monkeypatch, tmp_path)
    cold_payload, cold_audit = capture.raw46_features(snapshot(), Path("unused.db"))
    fast_payload, fast_audit = capture.prepared_raw46_features(snapshot(), prepared)
    assert cold_payload["columns"] == fast_payload["columns"]
    assert cold_payload["values"] == fast_payload["values"]
    assert cold_payload["input_sha256"] == fast_payload["input_sha256"]
    assert fast_audit["history_rows"] == len(history)
    cold, cold_objects = build(monkeypatch, feature_builder=lambda _card, _db: (cold_payload, cold_audit))
    fast, fast_objects = build(monkeypatch, feature_builder=lambda _card, _db: (fast_payload, fast_audit))
    cold = capture.attach_direct_features(cold, cold_objects)
    fast = capture.attach_direct_features(fast, fast_objects)
    assert cold["features_extended"]["columns"] == fast["features_extended"]["columns"]
    assert cold["features_extended"]["values"] == fast["features_extended"]["values"]
    assert cold["features_extended"]["input_sha256"] == fast["features_extended"]["input_sha256"]
    assert fast["features_extended"]["formula_receipt"]["SQLite_read_at_cutoff"] is False
    assert fast["features_extended"]["formula_receipt"]["history_rebuilt_at_cutoff"] is False
    receipt = capture.persist_bundle(tmp_path / "research", fast, fast_objects)
    assert capture.read_bundle(Path(receipt["path"]))["features_extended"]["input_sha256"] == cold["features_extended"]["input_sha256"]


def test_warm_cutoff_never_scans_db_or_rebuilds_history_state(monkeypatch, tmp_path):
    prepared, _history = prepared_fixture(monkeypatch, tmp_path)
    from forward_adapters import feature_state
    from training import db_shared_features as shared

    def forbidden(*_args, **_kwargs):
        raise AssertionError("cold work forbidden at quote cutoff")

    monkeypatch.setattr(shared, "load_db_frames", forbidden)
    monkeypatch.setattr(shared, "_database", forbidden)
    monkeypatch.setattr(feature_state, "build_history_state", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    payload, audit = capture.prepared_raw46_features(snapshot(), prepared)
    bundle, objects = build(monkeypatch, feature_builder=lambda _card, _db: (payload, audit))
    attached = capture.attach_direct_features(bundle, objects)
    assert attached["features_extended"]["feature_count"] == 92


@pytest.mark.parametrize("name", ["state.json", "metadata.json", "history.parquet"])
def test_prepared_artifact_tampering_is_rejected(monkeypatch, tmp_path, name):
    prepared, _history = prepared_fixture(monkeypatch, tmp_path)
    with (prepared / name).open("ab") as stream:
        stream.write(b"x")
    with pytest.raises(capture.CaptureError, match="hash_mismatch"):
        capture.read_prepared_state(prepared, DAY)


def test_prepared_history_cannot_reuse_another_target_day(monkeypatch, tmp_path):
    prepared, _history = prepared_fixture(monkeypatch, tmp_path)
    with pytest.raises(capture.CaptureError, match="target_day_mismatch"):
        capture.read_prepared_state(prepared, "20261013")


def test_prepared_feature_sources_are_revalidated(monkeypatch, tmp_path):
    prepared, _history = prepared_fixture(monkeypatch, tmp_path)
    from training import db_shared_features as shared
    actual = shared.source_hashes()
    monkeypatch.setattr(shared, "source_hashes", lambda: {**actual, "training.db_shared_features": "0" * 64})
    with pytest.raises(capture.CaptureError, match="feature_source_mismatch"):
        capture.read_prepared_state(prepared, DAY)


def test_prepare_same_day_and_future_history_fails_before_cache_write(monkeypatch, tmp_path):
    from test_db_shared_win_direct_features import full_history
    from training import db_shared_features as shared
    history = full_history()
    history.loc[history.race_date.str.startswith("2026"), "race_date"] = DAY
    monkeypatch.setattr(shared, "load_db_frames", lambda *_args: (history, history.iloc[:0], {}))
    with pytest.raises(ValueError, match="strictly-before-target"):
        capture.prepare_history_state(Path("unused.db"), DAY, tmp_path / "research")
    assert not (tmp_path / "research").exists()


def test_prepare_cli_imports_standalone_package_roots_without_scraper_side_effect(tmp_path):
    database = tmp_path / "empty.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE races_ultimate(race_id TEXT PRIMARY KEY,data TEXT)")
        db.execute("CREATE TABLE race_results_ultimate(race_id TEXT,data TEXT)")
    before = capture.sha256(database.read_bytes())
    result = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "scripts/capture_db_shared_win_prestart.py"),
                             "prepare", "--target-date", DAY, "--db-path", str(database),
                             "--store", str(tmp_path / "research")], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "prepared" and payload["network_requests"] == 0
    assert payload["history_rows"] == 0
    assert capture.sha256(database.read_bytes()) == before


@pytest.mark.parametrize("field,value", [("yy", "25"), ("jyo", "05"), ("kai", "03"),
                                         ("nichi", "04"), ("rno", "12"), ("jyo", "unknown")])
def test_provider_body_race_identity_does_not_trust_only_requested_url(monkeypatch, field, value):
    inputs = sources()
    payload = json.loads(inputs[1]["body"])
    payload["data"].update(yy="26", jyo="08", kai="04", nichi="05", rno="11")
    payload["data"][field] = value
    inputs[1]["body"] = json.dumps(payload).encode()
    bundle, _ = build(monkeypatch, inputs=inputs)
    assert not bundle["capture_validation"]["point_in_time_verified"]
    assert any(reason.startswith("provider_odds_race_identity") for reason in bundle["capture_validation"]["reason_codes"])
