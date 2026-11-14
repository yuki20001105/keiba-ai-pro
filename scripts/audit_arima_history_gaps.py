"""Audit published career coverage for the three remaining Arima history gaps."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba"), str(ROOT / "scripts")]

import aiohttp
from bs4 import BeautifulSoup

import repair_pedigree_identities as repair
from experiments.common import file_sha256, read_json
from experiments.search import freeze_json
from scraping.fetch_pipeline import FetchAccessBlocked, fetch_context, fetch_text
from scraping.last3f_evidence import _text


def parse_profile(html, horse, target_day, columns):
    soup = BeautifulSoup(html, "lxml")
    canonical = [tag.get("href") or tag.get("content") for tag in
                 soup.select('link[rel="canonical"],meta[property="og:url"]')]
    if not canonical or any(repair.identity._profile_id(url) != horse for url in canonical):
        raise ValueError("profile-child-identity-unconfirmed")
    totals = []
    for row in soup.select("table.db_prof_table tr"):
        header, cell = row.find("th"), row.find("td")
        if header and cell and "通算成績" in _text(header.get_text()):
            match = re.match(r"(\d+)戦", _text(cell.get_text()))
            if match:
                totals.append(int(match[1]))
    tables = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        headers = [_text(cell.get_text()) for cell in rows[0].find_all("th")]
        if all(headers.count(name) == 1 for name in ("日付", "開催", "距離")):
            tables.append((rows, headers))
    if len(tables) != 1:
        raise ValueError("profile-history-table-not-unique")
    rows, headers = tables[0]
    dates, observations = set(), []
    for row in rows[1:]:
        cells = row.find_all("td")
        if not cells:
            continue
        if len(cells) <= max(headers.index(name) for name in ("日付", "開催", "距離")):
            raise ValueError("profile-history-row-truncated")
        day = re.fullmatch(r"(\d{4})/(\d{2})/(\d{2})", _text(cells[headers.index("日付")].get_text()))
        if day is None:
            raise ValueError("profile-history-date-unconfirmed")
        day = "".join(day.groups())
        if day in dates:
            raise ValueError("profile-history-date-duplicate")
        dates.add(day)
        venue = _text(cells[headers.index("開催")].get_text())
        course = _text(cells[headers.index("距離")].get_text())
        distance = re.fullmatch(r"(芝|ダ|障)(\d+)", course)
        if distance is None:
            raise ValueError("profile-history-course-unconfirmed")
        observations.append({"race_date": day, "venue": venue, "surface": distance[1],
                             "distance": int(distance[2])})
    prior = [row for row in observations if row["race_date"] < target_day]
    course_required = any("horse_course" in name for name in columns)
    matches = [row for row in prior if
               ("中山" in row["venue"] and row["surface"] == "芝" if course_required
                else row["distance"] >= 2300)]
    return {
        "horse_id": horse, "published_career_runs": totals[0] if len(totals) == 1 else None,
        "profile_history_rows": len(observations), "prior_rows": len(prior),
        "coverage_matches_published_career_count": len(totals) == 1 and totals[0] == len(observations),
        "prior_same_condition_rows": matches, "all_visible_history_rows": observations,
        "historical_prestart_availability_proven": False,
    }


async def run(root):
    remaining = read_json(root / "verification.json")["remaining"]
    results, errors = [], []
    archive = root / "career-sources.db"
    with closing(sqlite3.connect(archive)) as db, fetch_context(job_id=root.name + ":career") as metrics:
        db.execute("CREATE TABLE sources(url TEXT PRIMARY KEY,body BLOB,fetched_at REAL)")
        async with aiohttp.ClientSession() as session:
            for item in remaining:
                horse = item["horse_id"]
                url = f"https://db.netkeiba.com/horse/{horse}/"
                try:
                    response, html = await fetch_text(
                        session, url, max_retries=1, min_interval_sec=2.0, allow_redirects=False,
                        total_timeout_sec=30, max_body_bytes=2_000_000)
                except FetchAccessBlocked as exc:
                    errors.append({"horse_id": horse, "reason": str(exc)})
                    break
                captured = (response.cache_snapshot or {}).get("fetched_at")
                db.execute("INSERT INTO sources VALUES(?,?,?)", (url, response.body, captured))
                db.commit()
                if response.status != 200 or response.url != url or captured is None:
                    errors.append({"horse_id": horse, "reason": "profile-transport-unconfirmed"})
                    continue
                try:
                    result = parse_profile(html, horse, "20251228", item["columns"])
                except ValueError as exc:
                    errors.append({"horse_id": horse, "reason": str(exc)})
                    continue
                result.update(source_url=url, source_observed_at_unix=captured,
                              source_sha256=hashlib.sha256(response.body).hexdigest())
                results.append(result)
    result = {"results": results, "errors": errors, "network_requests": metrics.get("network_requests", 0)}
    freeze_json(root / "career-history-audit.json", result)
    freeze_json(root / "career-artifacts.json", {
        "source_archive_sha256": file_sha256(archive),
        "audit_sha256": file_sha256(root / "career-history-audit.json"),
        "code_sha256": file_sha256(Path(__file__)),
    })
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.root.resolve(strict=True))), ensure_ascii=False))


if __name__ == "__main__":
    main()
