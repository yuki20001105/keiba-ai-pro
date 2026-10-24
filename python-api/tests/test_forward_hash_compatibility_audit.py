"""New isolated read-only audit; never changes the frozen runtime source."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts/audit_forward_hash_compatibility.py"
SPEC = importlib.util.spec_from_file_location("forward_hash_compatibility_audit", MODULE_PATH)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def fixture_database(tmp_path, *, integer_rank_keys=True):
    path = tmp_path / "isolated.sqlite3"
    contract = {"paper_only": True, "protocol_sha256": "a" * 64,
                "artifact_hashes": {"model": "b" * 64}}
    rid, post = "202606040601", "2026-09-20T10:00:00+09:00"
    snapshot = {"race_id": rid, "post_time": post, "protocol_sha256": contract["protocol_sha256"],
                "artifact_hashes": contract["artifact_hashes"]}
    envelope = {"snapshot": snapshot, "snapshot_sha256": audit.digest(snapshot)}
    decision = {"decision": "paper_bet", "horse_number": 1, "stake_yen": 100}
    ranks = {n if integer_rank_keys else str(n): n for n in range(1, 11)}
    official = {"race_id": rid, "runner_numbers": list(range(1, 11)), "ranks": ranks,
                "winners": [1], "win_payouts": {"1": 270}, "status": "final"}
    settlement = {"race_id": rid, "mode": "prospective_paper", "actual_purchase": False,
                  "snapshot_sha256": envelope["snapshot_sha256"], "decision_sha256": audit.digest(decision),
                  "protocol_sha256": contract["protocol_sha256"], "artifact_hashes": contract["artifact_hashes"],
                  "official": official, "official_sha256": audit.digest(official), "payout_yen": 270}
    capture_payload = {"snapshot": envelope, "decision": decision, "decision_sha256": audit.digest(decision)}
    settlement_payload = {"settlement": settlement, "settlement_sha256": audit.digest(settlement)}
    events = []
    previous = None
    for seq, (kind, payload) in enumerate((("capture_committed", capture_payload),
                                         ("settlement_recorded", settlement_payload)), 1):
        body = {"recorded_at": "2026-09-20T01:15:00Z", "race_id": rid, "event_type": kind,
                "payload": payload, "previous_sha256": previous}
        h = audit.digest(body)
        events.append((seq, body["recorded_at"], rid, kind, audit.canonical(payload), previous, h))
        previous = h
    with sqlite3.connect(path) as db:
        db.executescript("""
        CREATE TABLE runtime_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE races(race_id TEXT PRIMARY KEY,status TEXT,post_time TEXT,snapshot_json TEXT,
          snapshot_sha256 TEXT,decision_json TEXT,decision_sha256 TEXT,settlement_json TEXT,settlement_sha256 TEXT);
        CREATE TABLE events(seq INTEGER PRIMARY KEY,recorded_at TEXT,race_id TEXT,event_type TEXT,
          payload_json TEXT,previous_sha256 TEXT,event_sha256 TEXT);
        """)
        db.executemany("INSERT INTO runtime_meta VALUES(?,?)", [("schema", "forward-runtime-v1"),
                         ("contract", audit.canonical(contract)), ("contract_sha256", audit.digest(contract))])
        db.execute("INSERT INTO races VALUES(?,?,?,?,?,?,?,?,?)", (rid, "settled", post, audit.canonical(envelope),
                   envelope["snapshot_sha256"], audit.canonical(decision), audit.digest(decision),
                   audit.canonical(settlement), audit.digest(settlement)))
        db.executemany("INSERT INTO events VALUES(?,?,?,?,?,?,?)", events)
    return path


def test_known_rank_key_roundtrip_is_exact_and_readonly(tmp_path):
    path = fixture_database(tmp_path)
    before = path.read_bytes()
    report = audit.audit_database(path)
    assert report["status"] == "PASS"
    assert report["strict_existing_guard_status"] == "FAIL_KNOWN_INTEGER_KEY_ROUNDTRIP"
    assert report["counts"]["settlement_hash_modes"] == {"exact_original_integer_rank_keys": 1}
    assert report["counts"]["event_hash_modes"] == {"strict": 1, "exact_original_integer_rank_keys": 1}
    assert path.read_bytes() == before
    assert report["guard_bypassed"] is False
    assert report["operational_performance_validated"] is False


def test_string_native_hashes_require_no_legacy_mode(tmp_path):
    report = audit.audit_database(fixture_database(tmp_path, integer_rank_keys=False))
    assert report["status"] == "PASS"
    assert report["strict_existing_guard_status"] == "PASS"
    assert report["counts"]["settlement_hash_modes"] == {"strict": 1}


@pytest.mark.parametrize("mapping", [{"01": 1}, {"19": 1}, {"1": True}, {"1": 19}, {1: 1, "2": 2}, {"1": 1.0}])
def test_does_not_guess_malformed_rank_map(mapping):
    with pytest.raises(ValueError):
        audit.restore_official_rank_keys({"ranks": mapping, "runner_numbers": list(range(1, 19))})


def test_only_official_ranks_keys_change():
    source = {"ranks": {"1": 1, "10": 2}, "runner_numbers": [1, 10],
              "win_payouts": {"10": 450}, "extra": {"1": 42}}
    before = deepcopy(source)
    restored = audit.restore_official_rank_keys(source)
    assert restored["ranks"] == {1: 1, 10: 2}
    assert restored["win_payouts"] == {"10": 450}
    assert restored["extra"] == {"1": 42}
    assert source == before


@pytest.mark.parametrize("tamper", ["projection_amount", "event_amount", "previous_link", "sequence", "capture", "contract", "official_race_id"])
def test_unknown_hash_changes_fail_closed(tmp_path, tamper):
    path = fixture_database(tmp_path)
    with sqlite3.connect(path) as db:
        if tamper in ("projection_amount", "official_race_id"):
            st = json.loads(db.execute("SELECT settlement_json FROM races").fetchone()[0])
            if tamper == "projection_amount": st["payout_yen"] = 100000
            else: st["official"]["race_id"] = "202606040602"
            db.execute("UPDATE races SET settlement_json=?", (audit.canonical(st),))
        elif tamper == "event_amount":
            payload = json.loads(db.execute("SELECT payload_json FROM events WHERE seq=2").fetchone()[0])
            payload["settlement"]["payout_yen"] = 100000
            db.execute("UPDATE events SET payload_json=? WHERE seq=2", (audit.canonical(payload),))
        elif tamper == "previous_link": db.execute("UPDATE events SET previous_sha256=? WHERE seq=2", ("f" * 64,))
        elif tamper == "sequence": db.execute("UPDATE events SET seq=3 WHERE seq=2")
        elif tamper == "capture": db.execute("UPDATE races SET snapshot_sha256=?", ("f" * 64,))
        elif tamper == "contract": db.execute("UPDATE runtime_meta SET value=? WHERE key='contract_sha256'", ("f" * 64,))
    report = audit.audit_database(path)
    assert report["status"] == "FAIL"
    assert report["counts"]["failures"] >= 1


def test_output_never_overwrites(tmp_path):
    output = tmp_path / "existing.json"
    output.write_text("preserve", encoding="utf-8")
    with pytest.raises(SystemExit):
        audit.main(["--root", str(tmp_path), "--output", str(output)])
    assert output.read_text(encoding="utf-8") == "preserve"
