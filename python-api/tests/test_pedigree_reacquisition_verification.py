from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from experiments.common import json_sha256
from test_pedigree_identity import HORSE, URL, page, repair_runner


@pytest.fixture
def verifier():
    script = Path(__file__).resolve().parents[2] / "scripts" / "verify_pedigree_reacquisition.py"
    spec = importlib.util.spec_from_file_location("verify_reacquisition_fixture", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def archive(tmp_path):
    path = tmp_path / "sources.db"
    body = page().encode()
    proof = repair_runner().proof_from_row(HORSE, (URL, URL, 200, body, 1000))
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE sources(url TEXT PRIMARY KEY,final_url TEXT,status INTEGER,body BLOB,fetched_at REAL);
            CREATE TABLE queue(horse_id TEXT PRIMARY KEY,status TEXT,proof_json TEXT);
        """)
        db.execute("INSERT INTO sources VALUES(?,?,?,?,?)", (URL, URL, 200, body, 1000))
        db.execute("INSERT INTO queue VALUES(?,?,?)", (HORSE, "accepted", json.dumps(proof)))
    return path, {HORSE: proof}


def test_replays_exact_source_and_queue_proof(verifier, archive):
    path, proofs = archive
    assert verifier.verify_sources(path, proofs) == {"accepted": 1}


def test_changed_source_bytes_are_rejected(verifier, archive):
    path, proofs = archive
    with sqlite3.connect(path) as db:
        db.execute("UPDATE sources SET body=?", (b"changed",))
    with pytest.raises(ValueError, match="source-bytes-or-transport-mismatch"):
        verifier.verify_sources(path, proofs)


def test_self_consistent_wrong_parent_claim_is_rejected_by_source_links(verifier, archive):
    path, proofs = archive
    proof = proofs[HORSE]
    proof["parents"]["damsire"]["horse_id"] = "000a099999"
    proof["proof_sha256"] = json_sha256({key: value for key, value in proof.items()
                                       if key != "proof_sha256"})
    with sqlite3.connect(path) as db:
        db.execute("UPDATE queue SET proof_json=?", (json.dumps(proof),))
    with pytest.raises(ValueError, match="parent-links-mismatch"):
        verifier.verify_sources(path, proofs)


def test_incomplete_collection_cannot_be_presented_as_finalized(verifier, tmp_path):
    with pytest.raises(ValueError, match="not-finalized"):
        verifier.verify(tmp_path)
