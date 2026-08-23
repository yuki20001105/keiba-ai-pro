from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "python-api" / "supabase_client.py"


class FakeQuery:
    def __init__(self, client: "FakeClient", table: str) -> None:
        self.client = client
        self.table = table

    def _record(self, operation: str, payload=None) -> "FakeQuery":
        self.client.calls.append((self.table, operation, payload))
        return self

    def upsert(self, payload):
        return self._record("upsert", payload)

    def insert(self, payload):
        return self._record("insert", payload)

    def delete(self):
        return self._record("delete")

    def eq(self, column, value):
        return self._record("eq", (column, value))

    def execute(self):
        return SimpleNamespace(data=[])


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object]] = []

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self, name)


def _module():
    spec = importlib.util.spec_from_file_location("phase3m_supabase_client", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _race(horse: dict) -> dict:
    return {
        "race_info": {"race_id": "202607200101", "race_name": "contract-race"},
        "horses": [horse],
        "payouts": [],
    }


def test_blob_writer_sends_json_objects_and_explicit_horse_number(monkeypatch) -> None:
    module = _module()
    client = FakeClient()
    monkeypatch.setattr(module, "get_client", lambda: client)

    horse = {"horse_num": 7, "horse_name": "contract-horse"}
    assert module.save_race_to_supabase(_race(horse)) is True

    race_upsert = next(
        payload
        for table, operation, payload in client.calls
        if table == "races_ultimate" and operation == "upsert"
    )
    assert isinstance(race_upsert["data"], dict)

    child_insert = next(
        payload
        for table, operation, payload in client.calls
        if table == "race_results_ultimate" and operation == "insert"
    )
    assert child_insert == [
        {
            "race_id": "202607200101",
            "horse_number": "7",
            "data": horse,
        }
    ]


def test_blob_writer_fails_closed_when_horse_number_is_missing(monkeypatch) -> None:
    module = _module()
    client = FakeClient()
    monkeypatch.setattr(module, "get_client", lambda: client)

    assert module.save_race_to_supabase(_race({"horse_name": "missing-number"})) is False
    assert not any(
        table == "race_results_ultimate" and operation == "insert"
        for table, operation, _payload in client.calls
    )


def test_model_metadata_writer_uses_json_object_and_reader_keeps_legacy_compatibility() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    upload = source.split("def upload_model_to_supabase", 1)[1].split(
        "def download_model_from_supabase", 1
    )[0]
    listing = source.split("def list_models_from_supabase", 1)[1].split(
        "def delete_model_from_supabase", 1
    )[0]

    assert '"metadata": metadata' in upload
    assert "json.dumps(metadata" not in upload
    assert "isinstance(raw_metadata, dict)" in listing
    assert "isinstance(raw_metadata, str)" in listing
