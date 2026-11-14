from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

from experiments.common import file_sha256, read_json
from experiments.dataset import build_dataset
from scraping import missingness_goal
from services.prediction_source_revision import initialize_source_revision
from test_experiment_dataset import database


def test_real_repair_loop_preserves_cohort_and_reports_unmet_zero_goal(tmp_path, monkeypatch):
    source = database(tmp_path / "source.db")
    with sqlite3.connect(source) as db:
        db.execute("ALTER TABLE races_ultimate ADD COLUMN created_at TEXT DEFAULT 'original'")
        db.execute("ALTER TABLE race_results_ultimate ADD COLUMN created_at TEXT DEFAULT 'original'")
        db.execute("CREATE TABLE horse_details(horse_id TEXT,sire TEXT,dam TEXT,damsire TEXT)")
        for row_id, raw in db.execute("SELECT id,data FROM race_results_ultimate").fetchall():
            item = json.loads(raw)
            if item["race_id"] == "201605010101":
                item.pop("sire")
                item.pop("damsire")
            db.execute("UPDATE race_results_ultimate SET data=? WHERE id=?", (json.dumps(item), row_id))
        raw, = db.execute("SELECT data FROM races_ultimate WHERE race_id='201605010101'").fetchone()
        meta = json.loads(raw)
        meta.update(race_class="不明", race_name="試験特別(2勝)")
        db.execute("UPDATE races_ultimate SET data=? WHERE race_id='201605010101'", (json.dumps(meta),))
    assert initialize_source_revision(source.resolve(), True)
    campaign = tmp_path / "baseline"
    campaign.mkdir()
    (campaign / "task-ledger.json").write_text(json.dumps({"snapshot": {"path": str(source)}}), encoding="utf-8")
    build_dataset(source, campaign / "dataset")
    # Reproduce the pre-fix saved dataset, not a new normalization result.
    dataset = campaign / "dataset" / "dataset.parquet"
    frame = pd.read_parquet(dataset)
    frame["safe_race_class"] = pd.Series(pd.NA, index=frame.index, dtype="string")
    frame.to_parquet(dataset, index=False)
    manifest_path = campaign / "dataset" / "dataset_manifest.json"
    manifest = read_json(manifest_path)
    manifest["dataset_sha256"] = file_sha256(dataset)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    cache = tmp_path / "keiba" / "data" / "pedigree_cache.db"
    cache.parent.mkdir(parents=True)
    with sqlite3.connect(cache) as db:
        db.execute("CREATE TABLE pedigree_cache(horse_id TEXT PRIMARY KEY,sire TEXT,dam TEXT,damsire TEXT)")
        db.executemany("INSERT INTO pedigree_cache VALUES(?,?,?,?)",
                       [("H1", "S", "A", "D"), ("H2", "S", "A", "D")])
    parent = tmp_path / "runs"
    monkeypatch.setattr(missingness_goal, "PARENT", parent)
    monkeypatch.setattr(missingness_goal, "_backup",
                        lambda _source, target: _copy_sqlite(cache, target))
    monkeypatch.setattr(missingness_goal, "_protected", lambda: {})
    original = file_sha256(source)
    root = parent / "missingness-fixture"
    result = missingness_goal.run(root, campaign)
    assert result["verified"] and not result["zero_missingness_goal_met"]
    assert result["summaries"][0]["features"]["safe_race_class"] == 2
    assert result["summaries"][1]["features"]["safe_race_class"] == 0
    assert result["summaries"][1]["features"]["safe_sire_starts"] == 2
    assert result["summaries"][2]["features"]["safe_sire_starts"] == 0
    assert result["pedigree_journal"]["changed_rows"] == 2
    assert file_sha256(source) == original
    assert missingness_goal.verify(root) == result
    (root / "clock-targets.json").write_text("[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="artifact-changed"):
        missingness_goal.verify(root)


def _copy_sqlite(source: Path, target: Path):
    with sqlite3.connect(source) as before, sqlite3.connect(target) as after:
        before.backup(after)


def test_clock_investigation_uses_only_strictly_earlier_cohort_rows():
    frame = pd.DataFrame([
        {"race_id": "prior", "horse_id": "H", "horse_number": 1, "race_date": "20250101",
         "safe_past3_count": 0, "safe_past3_avg_last3f_time": float("nan")},
        {"race_id": "target", "horse_id": "H", "horse_number": 2, "race_date": "20250201",
         "safe_past3_count": 1, "safe_past3_avg_last3f_time": float("nan")},
        {"race_id": "same-day", "horse_id": "H", "horse_number": 3, "race_date": "20250201",
         "safe_past3_count": 1, "safe_past3_avg_last3f_time": 35},
        {"race_id": "future", "horse_id": "H", "horse_number": 4, "race_date": "20250301",
         "safe_past3_count": 2, "safe_past3_avg_last3f_time": 35},
    ])
    findings = missingness_goal.clock_targets(frame)
    assert len(findings) == 1
    assert [row["race_id"] for row in findings[0]["prior_cohort_races"]] == ["prior"]


def test_public_pedigree_requires_exact_horse_identity_and_unambiguous_names():
    url = "https://db.netkeiba.com/horse/ped/2020000001/"
    html = f"""<link rel="canonical" href="{url}">
        <meta property="og:url" content="https://db.netkeiba.com//horse/2020000001/">
        <table class="blood_table"><tr><td><a>父</a></td></tr>
        <tr><td>ancestor</td></tr><tr><td><a>母</a></td><td><a>母父</a></td></tr>
        <tr><td>ancestor</td></tr></table>"""
    assert missingness_goal.public_pedigree(html, "2020000001", url) == {
        "sire": "父", "dam": "母", "damsire": "母父",
    }
    with pytest.raises(ValueError, match="identity-unconfirmed"):
        missingness_goal.public_pedigree(html, "2020000002", url)
    with pytest.raises(ValueError, match="ambiguous-or-incomplete"):
        missingness_goal.public_pedigree(html.replace(">父<", ">父 Father(米)<"), "2020000001", url)


def test_public_probe_stops_immediately_on_provider_block(tmp_path, monkeypatch):
    from scraping import fetch_pipeline

    calls = []

    async def blocked(_session, url, **kwargs):
        calls.append((url, kwargs))
        fetch_pipeline.record_fetch_metric("network_requests", url=url)
        raise fetch_pipeline.FetchAccessBlocked("HTTP 403")

    monkeypatch.setattr(fetch_pipeline, "fetch_text", blocked)
    audit = [{"horse_id": f"202000000{index}", "reason": "no_saved_pedigree", "affected_rows": 2}
             for index in range(3)]
    result = asyncio.run(missingness_goal._probe_pedigree(tmp_path, audit, tmp_path / "unused.db"))
    assert len(calls) == result["network_requests"] == 1
    assert result["stop_reason"] == "provider_blocked"
    assert calls[0][1]["allow_redirects"] is False


def test_successful_public_probe_uses_fetch_result_contract_and_isolates_cache(tmp_path, monkeypatch):
    from scraping import fetch_pipeline

    horse = "2020000001"
    url = f"https://db.netkeiba.com/horse/ped/{horse}/"
    html = f"""<link rel="canonical" href="{url}">
        <table class="blood_table"><tr><td><a>父</a></td></tr>
        <tr><td>ancestor</td></tr><tr><td><a>母</a></td><td><a>母父</a></td></tr>
        <tr><td>ancestor</td></tr></table>"""
    cache = tmp_path / "research-cache.db"
    with sqlite3.connect(cache) as db:
        db.execute("CREATE TABLE pedigree_cache(horse_id TEXT PRIMARY KEY,sire TEXT,dam TEXT,damsire TEXT)")

    async def fetched(_session, request_url, **_kwargs):
        assert request_url == url
        return fetch_pipeline.FetchResult(url, url, 200, html.encode(), "cache", 0), html

    monkeypatch.setattr(fetch_pipeline, "fetch_text", fetched)
    result = asyncio.run(missingness_goal._probe_pedigree(
        tmp_path, [{"horse_id": horse, "reason": "no_saved_pedigree", "affected_rows": 2}], cache
    ))
    assert result["network_requests"] == 0
    assert result["receipts"][0]["status"] == "research_cache_enriched"
    with sqlite3.connect(cache) as db:
        assert db.execute("SELECT sire,dam,damsire FROM pedigree_cache").fetchone() == ("父", "母", "母父")


def test_clock_probe_binds_original_response_and_all_saved_runner_identities(tmp_path, monkeypatch):
    from scraping import fetch_pipeline, last3f_evidence

    source = database(tmp_path / "source.db")
    race_id = "201605010101"
    url = f"https://db.netkeiba.com/race/{race_id}/"

    async def fetched(_session, request_url, **_kwargs):
        assert request_url == url
        return fetch_pipeline.FetchResult(url, url, 200, b"original", "cache", 0), "decoded"

    def parsed(html, **kwargs):
        assert html == "decoded" and kwargs["raw_body"] == b"original"
        assert {item["horse_id"] for item in kwargs["expected_runners"]} == {"H1", "H2"}
        return {"rows": [{"horse_id": "H1", "last3f_state": "missing", "source_raw": ""}],
                "source": {"observed_at_utc": kwargs["observed_at_utc"]}}

    monkeypatch.setattr(fetch_pipeline, "fetch_text", fetched)
    monkeypatch.setattr(fetch_pipeline, "get_fetch_control_status", lambda: {})
    monkeypatch.setattr(last3f_evidence, "parse_last3f_evidence", parsed)
    result = asyncio.run(missingness_goal._probe_clock(tmp_path, [{
        "horse_id": "H1", "prior_cohort_races": [{"race_id": race_id, "race_date": "20160110"}],
    }], source))
    assert result["network_requests"] == 0 and result["whole_race_identity_verified"]
    assert result["published_clock"] == "missing"
    assert result["historical_capture_time_verified"] is False
