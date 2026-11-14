"""Ordinary acquisition only: current public career tables, strict target cutoff."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import unicodedata
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from scraping.acquisition_scope import is_jra_race_id, normalized_date
from scraping.horse_history_ajax import combine_profile_history
from scraping.saved_horse_reuse import _present

HISTORY_INVENTORY_VERSION = "public-prior-race-links-v2"


class ProfileIdentityConflict(ValueError):
    pass


def validate_profile_child(profile: str, horse_id: str) -> None:
    from scraping.pedigree_identity import _profile_id
    soup = BeautifulSoup(profile, "lxml")
    identities = [tag.get("href") or tag.get("content") for tag in
                  soup.select('link[rel="canonical"],meta[property="og:url"]')]
    if not identities or any(_profile_id(value) != horse_id for value in identities):
        raise ProfileIdentityConflict("acquisition-history-child-conflict")


def has_history_table(html: str) -> bool:
    soup = BeautifulSoup(html, "lxml")
    return bool(_history_tables(soup))


def without_history_tables(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for table in _history_tables(soup):
        table.decompose()
    return str(soup)


def _history_tables(soup):
    return [table for table in soup.find_all("table")
            if "日付" in [th.get_text(strip=True) for th in table.find_all("th")]
            and any(th.get_text(strip=True) in {"着順", "着"} for th in table.find_all("th"))]


def prepare_history(profile: str, response: str | None, horse_id: str, cutoff: str) -> tuple[str, dict]:
    if normalized_date(cutoff) != cutoff:
        raise ValueError("acquisition-history-cutoff-invalid")
    validate_profile_child(profile, horse_id)
    html = combine_profile_history(profile, response, horse_id) if response is not None else profile
    soup = BeautifulSoup(html, "lxml")
    tables = _history_tables(soup)
    if len(tables) != 1:
        raise ValueError("acquisition-history-table-not-unique")
    table = tables[0]
    published = re.search(r"(\d+)戦\s*\d+勝", soup.get_text(" ", strip=True))
    count = int(published[1]) if published else None
    headers = [th.get_text(strip=True) for th in table.find_all("th")]
    if headers.count("日付") != 1:
        raise ValueError("acquisition-history-date-column-not-unique")
    date_index = headers.index("日付")
    finish_indices = [i for i, name in enumerate(headers) if name in {"着順", "着"}]
    if len(finish_indices) != 1:
        raise ValueError("acquisition-history-finish-column-not-unique")
    finish_index = finish_indices[0]
    prior, excluded = 0, 0
    nonstarts, prior_started, unknown_prior = 0, 0, 0
    inventory = []
    linked_ids = set()
    data_rows = [row for row in table.find_all("tr") if row.find("td")]
    for row in data_rows:
        cells = row.find_all("td")
        if max(date_index, finish_index) >= len(cells):
            raise ValueError("acquisition-history-row-incomplete")
        day = normalized_date(cells[date_index].get_text(strip=True))
        if day is None:
            raise ValueError("acquisition-history-date-invalid")
        finish = unicodedata.normalize("NFKC", cells[finish_index].get_text(strip=True))
        not_started = finish in {"取", "取消", "除", "除外"}
        started = (finish.isdecimal() and int(finish) > 0) or finish in {"中", "中止", "失", "失格"}
        nonstarts += int(not_started)
        if day < cutoff:
            prior += 1
            prior_started += int(started)
            unknown_prior += int(not (started or not_started))
            links = set()
            for anchor in row.find_all("a", href=True):
                parsed = urlsplit(anchor["href"])
                match = re.fullmatch(r"/race/(\d{12})/?", parsed.path)
                if (match and parsed.scheme in {"", "https", "http"}
                        and parsed.hostname in {None, "db.netkeiba.com"}
                        and not parsed.query and not parsed.fragment):
                    links.add(match[1])
            if len(links) > 1:
                raise ValueError("acquisition-history-race-link-conflict")
            race_id = next(iter(links)) if links else None
            if race_id is not None:
                if race_id in linked_ids or race_id[:4] != day[:4]:
                    raise ValueError("acquisition-history-race-link-identity-invalid")
                linked_ids.add(race_id)
            inventory.append({
                "race_id": race_id, "race_date": day,
                "start_status": "not_started" if not_started else "started" if started else "unknown",
                "scope": ("jra" if is_jra_race_id(race_id, day) else "outside_jra")
                if race_id is not None else "unlinked",
            })
        else:
            row.decompose()
            excluded += 1
    # Public career tables are newest first; assert instead of reordering silently.
    prior_dates = [normalized_date(row.find_all("td")[date_index].get_text(strip=True))
                   for row in table.find_all("tr") if row.find("td")]
    if prior_dates != sorted(prior_dates, reverse=True):
        raise ValueError("acquisition-history-order-invalid")
    return str(soup), {
        "schema": "ordinary-acquisition-career-v1", "status": "validated",
        "target_date": cutoff, "visible_history_rows": prior + excluded,
        "strictly_prior_rows": prior, "excluded_current_or_future_rows": excluded,
        "published_career_runs": count,
        "explicit_not_started_rows": nonstarts,
        "strictly_prior_started_rows": prior_started, "unknown_prior_start_status_rows": unknown_prior,
        "coverage_matches_published_career_count": count is not None and count == prior + excluded - nonstarts,
        "history_inventory_version": HISTORY_INVENTORY_VERSION,
        "strictly_prior_race_inventory": inventory,
        "prior_race_links_complete": all(item["race_id"] is not None for item in inventory),
        "prior_results_imported": False,
        "historical_prestart_capture_proven": False,
    }


def enrichment_repair_races(database: Path, race_ids: list[str], target_date: str) -> set[str]:
    """Revisit enrichment gaps even when settled results passed the date gate."""
    if not race_ids:
        return set()
    placeholders = ",".join("?" for _ in race_ids)
    repairs = set()
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        for race_id, body in db.execute(
            f"SELECT race_id,data FROM races_ultimate WHERE race_id IN ({placeholders})", race_ids,
        ):
            if not _present(json.loads(body).get("race_class")):
                repairs.add(race_id)
        for race_id, body in db.execute(
            f"SELECT race_id,data FROM race_results_ultimate WHERE race_id IN ({placeholders})", race_ids,
        ):
            horse = json.loads(body)
            if not all(_present(horse.get(field)) for field in ("sire", "dam", "damsire")):
                repairs.add(race_id)
            if is_jra_race_id(race_id, target_date):
                evidence = horse.get("_career_history_source")
                if (not isinstance(evidence, dict) or evidence.get("status") != "validated"
                        or evidence.get("target_date") != target_date
                        or evidence.get("history_inventory_version") != HISTORY_INVENTORY_VERSION
                        or not evidence.get("prior_race_links_complete")
                        or not evidence.get("coverage_matches_published_career_count")):
                    repairs.add(race_id)
    return repairs
