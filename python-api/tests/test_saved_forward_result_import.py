"""Offline parser and immutable original-receipt replay fixtures only."""
import asyncio
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/import_saved_forward_results.py"
spec = importlib.util.spec_from_file_location("saved_forward_import_under_test", SCRIPT)
cli = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = cli
spec.loader.exec_module(cli)
RID, DAY = "202605040601", "20261017"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def saved(tmp_path, monkeypatch):
    from forward_runtime import provider
    runtime = tmp_path / "runtime"
    (runtime / "objects").mkdir(parents=True)
    (runtime / "receipts").mkdir()
    write_json(runtime / "protocol.json", {"protocol_sha256": "a"*64})
    run = tmp_path / "replay"; run.mkdir()
    url = f"https://race.netkeiba.com/race/result.html?race_id={RID}"
    headers = ["着順", "枠番", "馬番", "馬名", "性齢", "斤量", "騎手", "タイム", "着差", "単勝", "人気", "馬体重"]
    horses = ''.join(f'<tr><td>{i}</td><td>1</td><td>{i}</td><td><a href="/horse/202000000{i}/">Horse {i}</a></td><td>牡3</td><td>57</td><td><a href="/jockey/100/">Jockey</a></td><td>1:34.5</td><td></td><td>{i+2}.0</td><td>{i}</td><td>470(+2)</td></tr>' for i in (1,2,3))
    html = '<html><head><title>Result 2026年10月17日</title></head><body><h1>3歳1勝クラス</h1><p class="smalltxt">2026年10月17日 芝左1600m / 天候:晴 / 芝:良 / 3頭</p><table class="RaceTable01"><tr>' + ''.join('<th>'+h+'</th>' for h in headers) + '</tr>' + horses + '</table></body></html>'
    raw = html.encode()
    bodysha = cli.hashlib.sha256(raw).hexdigest()
    obj = runtime / "objects" / bodysha; obj.write_bytes(raw)
    receipt = {"request_url": url, "url": url, "source": "network", "status": 200,
        "request_started_at": "2026-10-17T07:35:00Z", "received_at": "2026-10-17T07:35:01Z",
        "body_sha256": bodysha, "body_size": len(raw), "protocol_sha256": "a"*64}
    receipt["receipt_sha256"] = cli.preparation.object_hash(receipt)
    rp = runtime / "receipts" / (receipt["receipt_sha256"] + '.json')
    write_json(rp, receipt)
    item = {"race_id": RID, "race_date": DAY, "post_time": "2026-10-17T07:30:00Z", "venue": "05",
        "source": {"receipt_path": str(rp), "object_path": str(obj), "body_sha256": bodysha,
            "receipt_sha256": receipt["receipt_sha256"],
            "observed_at_utc": receipt["received_at"], "receipt_verified": True, "object_hash_verified": True}}
    official = {"race_id": RID, "complete": True, "status": "final", "win_payouts": {"1":300}}
    monkeypatch.setattr(provider, "parse_result", lambda *args, **kw: official)
    return {"runtime": runtime, "run": run, "item": item, "raw": raw, "object": obj,
        "receipt": rp, "official": official, "html": html}


def replay(saved):
    return asyncio.run(cli.replay_candidate(saved["item"], run_dir=saved["run"], runtime_root=saved["runtime"]))


def test_real_scraper_replays_one_raw_with_no_network_or_original_mutation(saved):
    from scraping import race
    before = (saved["object"].read_bytes(), saved["receipt"].read_bytes())
    original_fetch = race.fetch_text
    candidate, proof = replay(saved)
    assert len(candidate["horses"]) == 3
    assert proof["network_requests"] == 0
    assert proof["source_observed_at_utc"] == "2026-10-17T07:35:01Z"
    assert proof["replayed_at_utc"] != proof["source_observed_at_utc"]
    assert candidate["_source_evidence"]["replay_is_new_source_observation"] is False
    assert candidate["_source_evidence"]["observed_at_utc"] == proof["source_observed_at_utc"]
    assert race.fetch_text is original_fetch
    assert before == (saved["object"].read_bytes(), saved["receipt"].read_bytes())


def test_unverified_payout_not_saved_or_zero_filled(saved):
    candidate, proof = replay(saved)
    assert candidate["return_tables"] == []
    assert proof["payout_status"] == "pending_unverified_parser_shape"


def test_source_body_tamper_is_rejected(saved):
    with saved["object"].open('ab') as stream:
        stream.write(b'tamper')
    with pytest.raises(cli.RepairConflict, match="url-body-or-network"):
        replay(saved)


def test_original_receipt_tamper_is_rejected(saved):
    value = cli.preparation.read_json(saved["receipt"])
    value['received_at'] = '2026-10-17T08:35:01Z'
    write_json(saved["receipt"], value)
    with pytest.raises(cli.RepairConflict, match="receipt-seal"):
        replay(saved)


def test_wrong_original_observation_cannot_be_backdated(saved):
    saved["item"]["source"]["observed_at_utc"] = '2026-10-16T08:35:01Z'
    with pytest.raises(cli.RepairConflict, match="observation-time"):
        replay(saved)


def test_absent_source_is_pending_not_fabricated(saved):
    saved["item"]["source"] = None
    with pytest.raises(cli.RepairConflict, match="not-available"):
        replay(saved)


def test_missing_metadata_stays_invalid_not_filled_from_a_date_hint(saved):
    # Exact same raw result rows, but no displayed course metadata. The source
    # is resealed as a synthetic fixture; the production parser must reject it.
    html = saved["html"].replace(' 芝左1600m / 天候:晴 / 芝:良 / 3頭', '')
    raw = html.encode(); sha = cli.hashlib.sha256(raw).hexdigest()
    obj = saved["runtime"] / 'objects' / sha; obj.write_bytes(raw)
    receipt = cli.preparation.read_json(saved["receipt"])
    receipt['body_sha256'] = sha; receipt['body_size'] = len(raw)
    receipt['receipt_sha256'] = cli.preparation.object_hash({k:v for k,v in receipt.items() if k!='receipt_sha256'})
    rp = saved["runtime"] / 'receipts' / (receipt['receipt_sha256']+'.json'); write_json(rp, receipt)
    saved['item']['source'].update(object_path=str(obj), receipt_path=str(rp), body_sha256=sha, receipt_sha256=receipt['receipt_sha256'])
    with pytest.raises((cli.RepairConflict, ValueError)):
        replay(saved)


def test_original_pending_result_is_not_imported(saved):
    saved['official']['complete'] = False
    with pytest.raises(cli.RepairConflict, match="not-final"):
        replay(saved)


def test_offline_session_rejects_all_network_methods():
    for name in ('get', 'post', 'request'):
        with pytest.raises(cli.RepairConflict, match="network-session-forbidden"):
            getattr(cli.NoNetworkSession(), name)


def test_replay_helper_rejects_entry_or_other_endpoint_and_restores_hooks(saved, monkeypatch):
    from scraping import race
    before = race.fetch_text
    async def illicit(session, rid, **kwargs):
        await race.fetch_text(session, f'https://race.netkeiba.com/race/shutuba.html?race_id={rid}')
    monkeypatch.setattr(race, 'scrape_race_full', illicit)
    with pytest.raises(cli.RepairConflict, match="one-raw-only"):
        replay(saved)
    assert race.fetch_text is before


def test_replay_calls_frozen_parser_with_no_date_hint(saved, monkeypatch):
    from scraping import race
    original = race.scrape_race_full
    calls = []
    async def spy(session, rid, **kwargs):
        calls.append(kwargs)
        return await original(session, rid, **kwargs)
    monkeypatch.setattr(race, 'scrape_race_full', spy)
    replay(saved)
    assert calls[0]['date_hint'] == ''
    assert calls[0]['results_required'] is True and calls[0]['enrich_horse_details'] is False


def test_changed_or_missing_plan_code_freeze_blocks_before_apply(saved):
    for freeze in (None, {"parser.py": "changed"}):
        write_json(saved['run'] / 'plan.json', {"schema": cli.SCHEMA, "implementation_sha256": freeze})
        with pytest.raises(ValueError, match="code-freeze-changed-or-missing"):
            asyncio.run(cli.process(saved['run'], apply=True))
        assert not (saved['run'] / 'apply.claim.json').exists()


def test_implementation_hashes_detect_post_import_disk_change(monkeypatch):
    original = cli.preparation.file_hash
    def changed(path):
        if Path(path).resolve() == SCRIPT.resolve():
            return '0' * 64
        return original(path)
    monkeypatch.setattr(cli.preparation, 'file_hash', changed)
    with pytest.raises(ValueError, match="implementation-changed-since-process-start"):
        cli.implementation_hashes()

