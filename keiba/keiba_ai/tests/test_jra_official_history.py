from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from keiba_ai.db_ultimate_loader import load_ultimate_training_frame
from keiba_ai.jra_official_history import (
    JRA_TERMS_URL,
    apply_official_results_import,
    discover_source_index,
    download_source_index,
    normalize_jra_pdf_text,
    parse_official_result_crop,
    prepare_official_results_import,
)
from scripts.audit_phase3n_research_data import audit


class _Response:
    content = b'<a href="2024/2024-1sapporo1.pdf">1 day</a>'

    def raise_for_status(self) -> None:
        return None


class _PdfResponse:
    content = b"%PDF-pilot"
    headers = {"content-type": "application/pdf"}

    def raise_for_status(self) -> None:
        return None


class _Crop:
    def extract_text(self, **_: object) -> str:
        return (
            "20001 7月20日 晴 良 （2024年1札幌）第1日 第 1 競走 2歳未勝利 1，200\n"
            "発走9時50分 （芝・右）\n負担重量は，馬齢重量"
        )

    def extract_tables(self) -> list[list[list[str | None]]]:
        row: list[str | None] = [None] * 23
        row[1] = "5\n2\n3\n1\n4"
        row[2] = "ポッドベイダー\nニシノクードクール\nロードヴェルト\nルージュアマリア\nロードヴァルカン"
        row[20] = "1：08．8\n1：09．1\n1：09．4\n1：10．0\n1：10．1"
        row[22] = "1．2\n10．2\n7．9\n5．9\n21．3"
        return [[row]]


class _JumpCrop:
    def extract_text(self, **_: object) -> str:
        return (
            "15080 6月22日 小雨 稍重 （2019年3東京）第7日 第8競走\n"
            "第21回東京ジャンプステークス（Ｊ・ＧⅢ） 3，110\n"
            "発走14時00分 （ 芝 ）\n負担重量は，別定"
        )

    def extract_tables(self) -> list[list[list[str | None]]]:
        raise AssertionError("jump races must be excluded before table parsing")


def _bundle(root: Path, records: list[dict[str, object]]) -> Path:
    data = root / "runners.jsonl"
    data.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records),
        encoding="utf-8",
    )
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "jra-official-results-manifest-v1",
                "provider": "JRA official annual results",
                "terms_url": JRA_TERMS_URL,
                "purpose": "internal-research-speed-deviation-outcomes",
                "complete_races": True,
                "point_in_time_odds_included": False,
                "files": [
                    {
                        "path": data.name,
                        "sha256": hashlib.sha256(data.read_bytes()).hexdigest(),
                        "format": "jsonl",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return manifest


def _database(path: Path) -> Path:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE race_results_ultimate (id INTEGER PRIMARY KEY, race_id TEXT, data TEXT)"
        )
        connection.execute("CREATE TABLE races_ultimate (race_id TEXT PRIMARY KEY, data TEXT)")
        connection.execute(
            "INSERT INTO race_results_ultimate (race_id, data) VALUES (?, ?)",
            (
                "202407200101",
                json.dumps({"race_id": "202407200101", "horse_id": "legacy"}),
            ),
        )
        connection.execute(
            "INSERT INTO race_results_ultimate (race_id, data) VALUES (?, ?)",
            (
                "201801010101",
                json.dumps(
                    {
                        "race_id": "201801010101",
                        "horse_id": "legacy-other-race",
                        "time": "1:09.9",
                    }
                ),
            ),
        )
    return path


def test_source_index_is_official_host_only() -> None:
    result = discover_source_index(
        [2024], get=lambda *_args, **_kwargs: _Response(), fetched_at="2026-08-15T00:00:00Z"
    )
    assert result["terms_url"] == JRA_TERMS_URL
    assert result["documents"] == [
        {
            "year": 2024,
            "url": "https://www.jra.go.jp/datafile/seiseki/report/2024/2024-1sapporo1.pdf",
            "label": "1 day",
            "filename": "2024-1sapporo1.pdf",
        }
    ]


def test_downloader_hashes_pdf_and_reuses_verified_bytes(tmp_path: Path) -> None:
    index = discover_source_index(
        [2024], get=lambda *_args, **_kwargs: _Response(), fetched_at="2026-08-15T00:00:00Z"
    )
    calls = []

    def get(url: str, **_kwargs: object) -> _PdfResponse:
        calls.append(url)
        return _PdfResponse()

    first = download_source_index(index, tmp_path, get=get, delay_seconds=0.5, workers=2)
    second = download_source_index(index, tmp_path, get=get, delay_seconds=0.5, workers=2)
    assert len(calls) == 1
    assert first["download_complete"] is True
    assert first["downloaded_documents"][0]["sha256"] == hashlib.sha256(
        _PdfResponse.content
    ).hexdigest()
    assert second["downloaded_documents"][0]["reused_existing_bytes"] is True


def test_crop_parser_separates_final_odds_from_training_payload(tmp_path: Path) -> None:
    digest = "a" * 64
    records, report = parse_official_result_crop(
        _Crop(),
        source_url="https://www.jra.go.jp/datafile/seiseki/report/2024/sample.pdf",
        source_pdf_sha256=digest,
        page_number=1,
        column="left",
    )
    assert report["runner_count"] == 5
    assert records[0]["race_id"] == "202407200101"
    assert records[0]["post_time"] == "2024-07-20T00:50:00+00:00"
    assert records[0]["payload"]["time_seconds"] == 68.8
    assert records[0]["result_win_odds"] == 1.2
    assert "odds" not in records[0]["payload"]

    prepared = prepare_official_results_import(_bundle(tmp_path, records))
    database = _database(tmp_path / "research.db")
    first = apply_official_results_import(database, prepared)
    second = apply_official_results_import(database, prepared)
    assert first["inserted_record_count"] == 5
    assert second["idempotently_skipped_record_count"] == 5
    loaded = load_ultimate_training_frame(database)
    official = loaded[loaded["race_id"].eq("202407200101")]
    assert len(official) == 5
    assert official["official_source_provider"].eq("JRA official annual results").all()
    assert official["time_seconds"].eq(68.8).any()
    assert "odds" not in loaded.columns
    with sqlite3.connect(database) as connection:
        stored_odds = connection.execute(
            "SELECT result_win_odds FROM official_history_entries ORDER BY rowid LIMIT 1"
        ).fetchone()[0]
    assert stored_odds == 1.2
    readiness = audit(database, minimum_annual_races=1)
    assert readiness["official_result_entry_count"] == 5
    assert readiness["coverage_by_year"]["2024"]["authorized_outcome_races"] == 1
    assert readiness["ready_for_speed_deviation_walk_forward"] is False
    assert readiness["ready_for_oof_value_evaluation"] is False


def test_manifest_cannot_smuggle_final_odds_into_payload(tmp_path: Path) -> None:
    records, _ = parse_official_result_crop(
        _Crop(),
        source_url="https://www.jra.go.jp/datafile/seiseki/report/2024/sample.pdf",
        source_pdf_sha256="b" * 64,
        page_number=1,
        column="left",
    )
    records[0]["payload"]["odds"] = 1.2
    with pytest.raises(ValueError, match="must not enter the training payload"):
        prepare_official_results_import(_bundle(tmp_path, records))


def test_cid_digit_normalization_is_explicit() -> None:
    assert normalize_jra_pdf_text("(cid:9873)，(cid:9874)(cid:9872)(cid:9872)") == "1,200"


def test_named_jump_stakes_is_audited_and_excluded() -> None:
    records, report = parse_official_result_crop(
        _JumpCrop(),
        source_url="https://www.jra.go.jp/datafile/seiseki/report/2019/sample.pdf",
        source_pdf_sha256="c" * 64,
        page_number=4,
        column="right",
    )
    assert records == []
    assert report == {"race_id": "201906220508", "status": "excluded-jump-race"}
