from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json
import time

import pytest
from bs4 import BeautifulSoup

from scraping import fetch_pipeline, horse, parsed_cache, required_public_inputs as required
from scraping.saved_horse_reuse import acquisition_horse_reuse
from scraping.storage_preservation import PreservationConflict, merge_acquisition_payload
from test_acquisition_parsed_reuse import HORSE_HTML, cached_source, successful_source
from test_pedigree_identity import HORSE, page


PROFILE = (f'<link rel="canonical" href="https://db.netkeiba.com/horse/{HORSE}/">'
           + HORSE_HTML.replace("5戦2勝", "2戦0勝"))


@pytest.fixture(autouse=True)
def isolated_sources(tmp_path, monkeypatch):
    parsed_cache.close_cache_connections()
    fetch_pipeline.close_fetch_cache_connections()
    monkeypatch.setattr(fetch_pipeline, "_CACHE_DB_PATH", tmp_path / "http.db")
    monkeypatch.setattr(parsed_cache, "_CACHE_PATH", tmp_path / "parsed.db")
    monkeypatch.setattr(horse, "_get_pedigree_sqlite", lambda _: None)
    yield
    parsed_cache.close_cache_connections()
    fetch_pipeline.close_fetch_cache_connections()


def source_fetcher(monkeypatch, *, profile=PROFILE, pedigree=None):
    calls = []

    async def fetch(_session, url, **kwargs):
        calls.append((url, kwargs))
        existing = fetch_pipeline._read_cache(url)
        if existing and not kwargs.get("force_refresh"):
            return cached_source(url, existing)
        body = (page() if pedigree is None else pedigree) if "/ped/" in url else profile
        return successful_source(url, body)

    monkeypatch.setattr(fetch_pipeline, "fetch_text", fetch)
    return calls


def collect(target="20250201", **kwargs):
    return asyncio.run(required.scrape_required_horse_inputs(
        None, HORSE, target, horse._parse_horse_source_html, **kwargs,
    ))


def test_only_required_fields_and_sources_survive_and_pedigree_is_reused(monkeypatch):
    calls = source_fetcher(monkeypatch)
    first = collect()
    assert first["prev_race_date"] == "2025/01/05"
    assert first["prev2_race_distance"] == 1600
    assert first["damsire_id"] == "000a010a17"
    assert first["damsire"] == "Sevres Rose\u00a0(愛)"
    assert first["_pedigree_source"]["fetch"] == {
        "http_status": 200, "transport_source": "network", "attempts": 1, "error": None,
    }
    assert not any(key.startswith("horse_") for key in first)
    assert first["_career_history_source"]["prior_results_imported"] is False
    second = collect("20250101")
    assert second["prev_race_date"] == "2024/12/01"
    assert "prev2_race_date" not in second
    assert second["_career_history_source"]["excluded_current_or_future_rows"] == 1
    assert second["_pedigree_source"]["fetch"] == first["_pedigree_source"]["fetch"]
    assert [url for url, _ in calls] == [
        f"https://db.netkeiba.com/horse/{HORSE}/",
        f"https://db.netkeiba.com/horse/ped/{HORSE}/",
        f"https://db.netkeiba.com/horse/{HORSE}/",
    ]


def test_mismatched_child_is_not_parsed_as_history_or_zero_experience(monkeypatch):
    calls = source_fetcher(monkeypatch, profile=PROFILE.replace(HORSE, "2099999999"))
    result = collect()
    assert result["_career_history_source"]["status"] == "unresolved"
    assert "strictly_prior_rows" not in result["_career_history_source"]
    assert not any(key.startswith("prev") for key in result)
    assert not any("ajax" in url for url, _ in calls)


def test_ajax_is_fetched_only_for_a_missing_public_results_table(monkeypatch):
    start = PROFILE.index("<table><tr><th>日付")
    table = PROFILE[start:PROFILE.index("</body>")]
    profile = PROFILE.replace(table, '<div id="horse_results_box"></div>')
    calls = []

    async def fetch(_session, url, **_kwargs):
        calls.append(url)
        body = (page() if "/ped/" in url else json.dumps({"status": "OK", "data": table})
                if "ajax_horse_results" in url else profile)
        return successful_source(url, body)

    monkeypatch.setattr(fetch_pipeline, "fetch_text", fetch)
    result = collect()
    assert result["prev2_race_date"] == "2024/12/01"
    assert result["_career_history_source"]["status"] == "validated"
    assert len(calls) == 3
    assert sum("ajax_horse_results" in url for url in calls) == 1
    assert not any("db.sp." in url or "/horse/result/" in url for url in calls)


def test_source_confirmed_zero_history_is_not_filled_with_fictional_statistics(monkeypatch):
    soup = BeautifulSoup(PROFILE.replace("2戦0勝", "0戦0勝"), "lxml")
    for table in soup.find_all("table"):
        if any(th.get_text(strip=True) == "日付" for th in table.find_all("th")):
            for row in table.find_all("tr"):
                if row.find("td"):
                    row.decompose()
    source_fetcher(monkeypatch, profile=str(soup))
    result = collect()
    assert result["_career_history_source"]["strictly_prior_rows"] == 0
    assert result["_required_public_inputs"]["public_career_coverage_verified"] is True
    assert not any(key.startswith(("prev", "horse_total")) for key in result)


def test_network_failure_is_explicit_and_never_becomes_a_debut(monkeypatch):
    calls = source_fetcher(monkeypatch)
    original = required.fetch_source_text

    async def fail_profile(session, url, **kwargs):
        if "/ped/" not in url:
            return fetch_pipeline.FetchResult(url, url, 0, b"", "network", 1), ""
        return await original(session, url, **kwargs)

    monkeypatch.setattr(required, "fetch_source_text", fail_profile)
    result = collect()
    assert result["_career_history_source"]["error"] == "profile-http-0"
    assert "published_career_runs" not in result["_career_history_source"]
    assert len(calls) == 1


@pytest.mark.parametrize("role", ["profile", "ajax", "pedigree"])
@pytest.mark.parametrize("status,error", [(0, "timeout"), (0, "total-timeout"), (503, None)])
def test_transport_failure_details_survive_for_each_required_source(monkeypatch, role, status, error):
    start = PROFILE.index("<table><tr><th>日付")
    table = PROFILE[start:PROFILE.index("</body>")]
    profile = (PROFILE.replace(table, '<div id="horse_results_box"></div>')
               if role == "ajax" else PROFILE)
    source_fetcher(monkeypatch, profile=profile)
    original = fetch_pipeline.fetch_text

    async def fetch(session, url, **kwargs):
        current_role = ("pedigree" if "/ped/" in url else "ajax"
                        if "ajax_horse_results" in url else "profile")
        if current_role == role:
            return fetch_pipeline.FetchResult(url, url, status, b"", "network-error", 2, error), ""
        return await original(session, url, **kwargs)

    monkeypatch.setattr(fetch_pipeline, "fetch_text", fetch)
    result = collect()
    if role == "pedigree":
        source = result["_pedigree_source"]
        metadata = source["fetch"]
        assert source["reason"] == f"pedigree-http-{status}"
        assert "sire" not in result
    else:
        source = result["_career_history_source"]
        metadata = source["history_fetch" if role == "ajax" else "profile_fetch"]
        assert source["error"] == f"{'career-ajax' if role == 'ajax' else 'profile'}-http-{status}"
        assert "strictly_prior_rows" not in source
        assert not any(key.startswith("prev") for key in result)
        assert result["_required_public_inputs"]["public_career_coverage_verified"] is False
    assert source["status"] == "unresolved"
    assert metadata == {
        "http_status": status, "transport_source": "network-error", "attempts": 2, "error": error,
    }


def test_successful_transport_does_not_make_rejected_pedigree_valid(monkeypatch):
    source_fetcher(monkeypatch, pedigree="<html><body>No parent table</body></html>")
    result = collect()
    source = result["_pedigree_source"]
    assert source["status"] == "unresolved"
    assert source["reason"].startswith("pedigree-source-")
    assert source["fetch"] == {
        "http_status": 200, "transport_source": "network", "attempts": 1, "error": None,
    }
    assert "sire" not in result
    assert result["_career_history_source"]["status"] == "validated"


def test_country_or_parent_conflict_keeps_observation_separate(monkeypatch):
    source_fetcher(monkeypatch)
    result = collect(current_pedigree={"damsire": "Sevres Rose"})
    assert result["_required_public_inputs"]["pedigree_source_status"] == "conflict"
    assert "sire" not in result and "damsire" not in result
    assert result["_pedigree_source"]["observed_parents"]["damsire"] == "Sevres Rose\u00a0(愛)"


def test_provider_block_stops_before_additional_sources(monkeypatch):
    calls = []

    async def blocked(_session, url, **_kwargs):
        calls.append(url)
        raise fetch_pipeline.FetchAccessBlocked("http-403")

    monkeypatch.setattr(required, "fetch_source_text", blocked)
    with pytest.raises(fetch_pipeline.FetchAccessBlocked, match="http-403"):
        collect()
    assert len(calls) == 1


def test_old_seven_day_body_is_refreshed_once_not_accepted_as_fresh(monkeypatch):
    url = f"https://db.netkeiba.com/horse/{HORSE}/"
    successful_source(url, PROFILE, ttl=7 * 24 * 60 * 60)
    with fetch_pipeline._cache_connection() as db:
        db.execute("UPDATE http_cache SET fetched_at=?", (time.time() - 7 * 60 * 60,))
        db.commit()
    calls = source_fetcher(monkeypatch)
    result = collect()
    assert result["_career_history_source"]["status"] == "validated"
    assert [kwargs.get("force_refresh", False) for called, kwargs in calls if called == url] == [False, True]


def test_job_scope_never_changes_recent_or_unscoped_inference(monkeypatch, tmp_path):
    historical = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")
    race_id = historical[:4] + "06050811"
    assert not required.required_public_target(HORSE, historical, race_id)
    with required.required_public_acquisition():
        assert required.required_public_target(HORSE, historical, race_id)
        assert required.required_public_target("000a010a17", historical, race_id)
        assert not required.required_public_target(HORSE, historical, historical[:4] + "45050811")
        assert not required.required_public_target(HORSE, historical)
        assert not required.required_public_target(HORSE, datetime.now().strftime("%Y%m%d"), race_id)
        assert not required.required_public_target("B123456", historical, race_id)
        with acquisition_horse_reuse(tmp_path / "unused.db", historical, race_id=race_id):
            calls = source_fetcher(monkeypatch)
            result = asyncio.run(horse.scrape_horse_detail(None, HORSE))
            assert result["_required_public_inputs"]["policy"] == required.PUBLIC_INPUTS_VERSION
            assert len(calls) == 2
    assert not required.required_public_target(HORSE, historical, race_id)


def test_saved_parent_alias_conflict_is_rejected_before_mutation():
    info = {"race_id": "202506050811", "date": "20251228"}
    existing = {"horse_id": HORSE, "horse_number": 1, "father": "既存の父"}
    incoming = {
        "race_info": info, "horses": [{
            "horse_id": HORSE, "horse_number": 1, "sire": "別の父",
            "_required_public_inputs": {"pedigree_source_status": "validated"},
        }],
    }
    with pytest.raises(PreservationConflict, match="required-public-parent-conflict:father"):
        merge_acquisition_payload(info, [existing], incoming)
    assert existing["father"] == "既存の父"
