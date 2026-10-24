"""Three explicitly bounded public source probes; source data DB is read-only."""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import time
import unicodedata
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python-api"))
import aiohttp
from bs4 import BeautifulSoup
from market_edge.compute import CollectorMonitor
from scraping.constants import get_random_headers
from scraping.fetch_pipeline import decode_html_body, fetch_bytes, fetch_context

RACE_IDS = ("202001010101", "202101010101", "202501020212")
ACCEPTED_CLOCK_HEADERS = {"上り", "上がり", "上り3F", "上がり3F", "後3F"}


def text(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")))


def clock(value):
    raw = text(value)
    if not re.fullmatch(r"\d{2}(?:\.\d{1,2})?", raw):
        return None
    number = float(raw)
    return number if 10 < number < 100 else None


def source_rows(html):
    soup = BeautifulSoup(html, "lxml")
    choices = []
    for table in soup.select("table.RaceTable01,table.race_table_01,table.ResultsByRaceDetail"):
        trs = table.find_all("tr")
        if not trs:
            continue
        headers = [text(cell.get_text(" ", strip=True)) for cell in trs[0].find_all(["th", "td"])]
        if headers.count("馬番") == 1 and headers.count("馬名") == 1:
            choices.append((trs, headers))
    if len(choices) != 1:
        raise ValueError("result-identity-table-not-unique")
    trs, headers = choices[0]
    nc, hc = headers.index("馬番"), headers.index("馬名")
    clocks = [i for i, name in enumerate(headers) if name in ACCEPTED_CLOCK_HEADERS]
    rows = []
    for tr in trs[1:]:
        cells = tr.find_all("td")
        if len(cells) <= max(nc, hc):
            continue
        num = text(cells[nc].get_text(strip=True))
        if not num.isdigit():
            raise ValueError("source-runner-number-unverified")
        links = []
        for anchor in cells[hc].find_all("a", href=True):
            match = re.search(r"/horse/(?:result/(?:recent/)?)?([A-Za-z0-9]+)(?:/|$)", urlsplit(anchor["href"]).path)
            if match:
                links.append((match[1], text(anchor.get_text(strip=True))))
        if len(links) != 1:
            raise ValueError("source-horse-link-not-unique")
        raw = cells[clocks[0]].get_text(strip=True) if len(clocks) == 1 and len(cells) > clocks[0] else None
        rows.append({"horse_number": int(num), "horse_id": links[0][0], "horse_name": links[0][1],
                     "clock_raw": raw, "clock_numeric": clock(raw)})
    return soup, headers, rows, clocks


def protect(monitor):
    state = monitor.snapshot()
    now = time.time()
    conflicts = [w["race_id"] for w in state.get("capture_windows", [])
                 if w["start"] - 210 <= now <= w["end"] + 60]
    if not state.get("healthy") or state.get("capturing") or conflicts:
        raise ValueError("collector-protected-or-unhealthy")
    return {"healthy": True, "protocol_sha256": state["protocol_sha256"], "observed_at": state["observed_at"],
            "conflicting_windows": conflicts, "collector_pid": state["pid"]}


async def main():
    suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "reports/generated/data-quality/public-last3f-probe" / suffix
    output.mkdir(parents=True, exist_ok=False)
    monitor = CollectorMonitor(ROOT / "reports/generated/forward-runtime/h04-live-20260920-v2")
    db = sqlite3.connect((ROOT / "keiba/data/keiba_ultimate.db").resolve().as_uri() + "?mode=ro", uri=True)
    db.execute("PRAGMA query_only=ON")
    records = []
    with fetch_context(job_id="public-last3f-three-probes-v1") as metrics:
        async with aiohttp.ClientSession(headers=get_random_headers(), cookie_jar=aiohttp.DummyCookieJar(),
                                         timeout=aiohttp.ClientTimeout(total=25)) as session:
            for race_id in RACE_IDS:
                saved = [json.loads(raw) for (raw,) in db.execute(
                    "SELECT data FROM race_results_ultimate WHERE race_id=?", (race_id,))]
                baseline = [{"horse_number": int(h["horse_number"]), "horse_id": str(h["horse_id"]),
                             "horse_name": text(h["horse_name"]),
                             "last_3f_raw": h.get("last_3f_time", h.get("last_3f"))} for h in saved]
                if not any("**" in str(h["last_3f_raw"]) for h in baseline):
                    raise ValueError("selected-race-not-actually-masked")
                url = "https://race.netkeiba.com/race/result.html?race_id=" + race_id
                item = {"race_id": race_id, "source_url": url, "saved_rows": baseline,
                        "pre_network_protection": protect(monitor), "started_at_utc": datetime.now(timezone.utc).isoformat()}
                result = await fetch_bytes(session, url, use_cache=False, force_refresh=True,
                                           max_retries=1, retry_statuses=set(), min_interval_sec=3.0,
                                           allow_redirects=False, total_timeout_sec=25, max_body_bytes=3_000_000)
                item.update(http_status=result.status, source=result.source, attempts=result.attempts,
                            observed_at_utc=datetime.now(timezone.utc).isoformat(), error=result.error,
                            body_sha256=hashlib.sha256(result.body).hexdigest())
                records.append(item)
                if result.status != 200:
                    item["decision"] = "STOP_STATUS_NOT_200"
                    break
                raw_path = output / (race_id + ".html")
                raw_path.write_bytes(result.body)
                item["body_path"] = str(raw_path.resolve())
                html = decode_html_body(result.body)
                try:
                    soup, headers, source, columns = source_rows(html)
                    item.update(headers=headers, source_rows=source,
                                clock_header=headers[columns[0]] if len(columns) == 1 else None)
                    expected = sorted((h["horse_number"], h["horse_id"], h["horse_name"]) for h in baseline)
                    actual = sorted((h["horse_number"], h["horse_id"], h["horse_name"]) for h in source)
                    item["all_runner_identity_verified"] = len(set(actual)) == len(actual) and actual == expected
                    canonical_ids = []
                    for tag in soup.select('link[rel="canonical"],meta[property="og:url"]'):
                        canonical_ids.extend(parse_qs(urlsplit(tag.get("href", tag.get("content", ""))).query).get("race_id", []))
                    item["canonical_ids"] = canonical_ids
                    if any(value != race_id for value in canonical_ids):
                        raise ValueError("canonical-race-id-conflict")
                    if not item["all_runner_identity_verified"]:
                        raise ValueError("all-runner-identity-mismatch")
                    if len(columns) != 1:
                        raise ValueError("clock-header-not-unique")
                    by_number = {h["horse_number"]: h for h in source}
                    counts = Counter()
                    for old in baseline:
                        current = by_number[old["horse_number"]]
                        counts["all_rows"] += 1
                        counts["source_clock_numeric"] += current["clock_numeric"] is not None
                        counts["source_clock_masked"] += "**" in str(current["clock_raw"])
                        counts["saved_masked_to_source_numeric"] += "**" in str(old["last_3f_raw"]) and current["clock_numeric"] is not None
                    item.update(counts=dict(counts), decision="PUBLIC_NUMERIC_CLOCK_RECOVERABLE" if counts["saved_masked_to_source_numeric"] else "NO_NUMERIC_RECOVERY")
                except ValueError as exc:
                    item.update(decision="SOURCE_UNVERIFIED", reason=str(exc))
                    break
        db.close()
        report = {"schema": "public-last3f-three-probes-v1", "created_at_utc": datetime.now(timezone.utc).isoformat(),
                  "fixed_race_ids": list(RACE_IDS), "maximum_http_requests": 3, "metrics": dict(metrics), "records": records,
                  "database_writes": 0, "cache_writes": False, "pacer_coordination_writes": True,
                  "credentials_or_cookies_used": False, "historical_capture_time_proven": False,
                  "model_or_h04_mutations": False, "scope": "Three public pages only; not a historical-wide availability estimate",
                  "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        (output / "probe.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output.resolve()), "records": [{k: r.get(k) for k in ("race_id", "http_status", "decision", "counts", "reason")} for r in records], "metrics": report["metrics"]}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
