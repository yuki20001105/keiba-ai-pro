"""Bounded, read-only recovery of source-bound historical lap observations.

Run with python-api\\.venv\\Scripts\\python.exe scripts\\recover_pace_laps.py.
Only existing JRA-looking legacy result HTTP-cache entries are inspected, one
body at a time. Both SQLite connections are read-only/query-only. No fetch,
scraper orchestration, model training or raw-HTML persistence is used.

Accepted identity is either publisher canonical/og:url, or a standalone inline
get_race_result_horse_laptime call naming the corresponding DOM render target.
Navigation links, generic race-ID arrays and transport URL alone never suffice.
The cache's capture time is not asserted to be historical pre-race availability.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import sys

from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "python-api") not in sys.path:
    sys.path.insert(0, str(ROOT / "python-api"))

from scraping.lap_times import _race_metadata, _seconds, parse_lap_times
from scraping.last3f_evidence import _day, _text, _url_race_id, _visible_metadata


PREFIX = "https://db.netkeiba.com/race/"
MAX_CANDIDATES = 3235
SCHEMA = "verified-pace-lap-recovery-v1"
_RENDER_CALL = re.compile(
    r"""^\s*get_race_result_horse_laptime\(\s*(['"])(\d{12})\1\s*,\s*(['"])"""
    r"""Race_Result_Horse_Laptime\3\s*,\s*1\s*\)\s*;\s*$""", re.MULTILINE)


def _decode_source(body):
    match = re.search(br"""charset\s*=\s*["']?([\w-]+)""", body[:8192], re.I)
    aliases = {"utf-8": "utf-8", "utf8": "utf-8", "euc-jp": "euc-jp",
               "euc_jp": "euc-jp", "shift_jis": "cp932", "shift-jis": "cp932",
               "sjis": "cp932", "cp932": "cp932", "windows-31j": "cp932"}
    if match:
        encoding = aliases.get(match[1].decode("ascii").lower())
        if encoding is None:
            raise ValueError("source_encoding_unsupported")
        try:
            return body.decode(encoding, errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError("source_encoding_invalid") from exc
    for encoding in ("utf-8", "euc-jp", "cp932"):
        try:
            return body.decode(encoding, errors="strict")
        except UnicodeDecodeError:
            continue
    raise ValueError("source_encoding_invalid")


def _page_identity(soup, race_id):
    canonical = [str(tag.get("href") or tag.get("content") or "")
                 for tag in soup.select('link[rel~=canonical],meta[property="og:url"]')]
    for url in canonical:
        try:
            stated = _url_race_id(url)
        except ValueError:
            return None, "page_identity_invalid"
        if stated != race_id:
            return None, "page_identity_mismatch"
    render_ids = []
    for script in soup.find_all("script"):
        if script.get("src") or str(script.get("type", "")).lower() not in {
                "", "text/javascript", "application/javascript"}:
            continue
        text = script.string or script.get_text()
        text = re.sub(r"`(?:\\.|[^`])*`", "", text, flags=re.DOTALL)
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        text = re.sub(r"//[^\n]*", "", text)
        render_ids.extend(match[2] for match in _RENDER_CALL.finditer(text))
    if any(value != race_id for value in render_ids):
        return None, "page_identity_mismatch"
    if canonical:
        return "publisher_canonical", None
    targets = soup.select("#Race_Result_Horse_Laptime")
    if len(render_ids) == 1 and len(targets) == 1:
        return "inline_result_laptime_render_call", None
    return None, "page_identity_missing"


def _winner_time(soup):
    tables = []
    for table in soup.select("table.race_table_01,table.RaceTable01"):
        rows = table.find_all("tr")
        if not rows:
            continue
        headers = [_text(cell.get_text()) for cell in rows[0].find_all(["th", "td"])]
        if headers.count("着順") == 1 and headers.count("タイム") == 1:
            tables.append((rows, headers.index("着順"), headers.index("タイム")))
    if len(tables) != 1:
        return None, "result_table_not_unique"
    rows, finish_column, time_column = tables[0]
    winners = []
    for row in rows[1:]:
        cells = row.find_all(["th", "td"])
        if len(cells) <= finish_column or _text(cells[finish_column].get_text()) != "1":
            continue
        if len(cells) <= time_column:
            return None, "displayed_winner_time_invalid"
        text = _text(cells[time_column].get_text())
        seconds = _seconds(text) if ":" in text else None
        if seconds is None:
            return None, "displayed_winner_time_invalid"
        winners.append(seconds)
    if not winners:
        return None, "displayed_winner_missing"
    if max(winners) - min(winners) > 0.11:
        return None, "displayed_winner_time_ambiguous"
    return min(winners), None


def recover_observation(body: bytes, *, source_url: str, final_url: str,
                        http_status: int, expected: dict | None) -> tuple[dict | None, str | None]:
    """Validate one raw cache body; return (compact observation, rejection reason)."""
    if isinstance(http_status, bool) or http_status != 200:
        return None, "http_not_success"
    if not expected:
        return None, "expected_metadata_missing"
    race_id = str(expected.get("race_id", ""))
    if not re.fullmatch(r"\d{12}", race_id) or not 1 <= int(race_id[4:6]) <= 10:
        return None, "expected_identity_invalid"
    try:
        identities = [_url_race_id(url) for url in (source_url, final_url)]
    except ValueError:
        return None, "transport_identity_invalid"
    if any(value != race_id for value in identities):
        return None, "transport_identity_mismatch"
    try:
        expected_date = _day(expected.get("date"))
    except ValueError:
        return None, "expected_date_invalid"
    if expected_date[:4] != race_id[:4]:
        return None, "expected_date_year_mismatch"
    if not isinstance(body, bytes) or not body or len(body) > 2 * 1024 * 1024:
        return None, "source_body_missing_or_oversized"
    try:
        html = _decode_source(body)
    except ValueError as exc:
        return None, str(exc)
    soup = BeautifulSoup(html, "lxml")
    method, reason = _page_identity(soup, race_id)
    if reason:
        return None, reason
    try:
        displayed_date, _ = _visible_metadata(soup)
    except ValueError:
        return None, "displayed_date_missing_or_ambiguous"
    if displayed_date != expected_date:
        return None, "displayed_date_mismatch"
    distances, obstacle = _race_metadata(soup)
    if obstacle:
        return None, "obstacle_laps_unsupported"
    if len(distances) != 1:
        return None, "displayed_distance_missing_or_ambiguous"
    if next(iter(distances)) != expected.get("distance"):
        return None, "displayed_distance_mismatch"
    winner, reason = _winner_time(soup)
    if reason:
        return None, reason
    # Remove result tables only from this parsed soup copy: the lap helper's
    # fastest-runner check differs from the recovery contract's displayed winner.
    lap_soup = BeautifulSoup(html, "lxml")
    for table in lap_soup.select("table.race_table_01,table.RaceTable01"):
        table.decompose()
    lap = parse_lap_times(lap_soup, race_distance=expected["distance"])
    if not lap["lap_sectional"]:
        return None, "laps_missing_or_invalid"
    evidence = lap["lap_evidence"]
    if evidence["sectional_source"] not in {"ラップ", "ハロンタイム"}:
        return None, "sectional_row_not_observed"
    total = list(lap["lap_cumulative"].values())[-1]
    if abs(total - winner) > 0.3 + 1e-9:
        return None, "winner_finish_mismatch"
    first600 = lap["lap_cumulative"].get(600)
    return {
        "race_id": race_id, "race_date": displayed_date, "first600_seconds": first600,
        "validated": first600 is not None,
        "source_sha256": hashlib.sha256(body).hexdigest(), "source_url": source_url,
        "lap_evidence": {**evidence, "sectional_seconds": lap["lap_sectional"],
                         "finish_time_check": "displayed_winner_matched",
                         "finish_time_seconds": winner},
        "validation": {"http_status_200": True, "transport_identity_match": True,
                       "page_identity_verified": True, "page_identity_method": method,
                       "displayed_date_verified": True, "expected_metadata_match": True,
                       "sectional_row_observed": True, "winner_finish_match": True,
                       "exact600_boundary": first600 is not None,
                       "historical_capture_time_proven": False},
        "validation_reasons": [] if first600 is not None else ["no_exact_600m_boundary"],
    }, None


def _coverage(observations):
    exact = [row for row in observations if row["first600_seconds"] is not None]
    return {"races": len(observations), "dates": len({row["race_date"] for row in observations}),
            "exact600_labels": len(exact), "exact600_dates": len({row["race_date"] for row in exact})}


def aggregate_recovery(observations, exclusions, *, candidate_count):
    years = sorted({row["race_date"][:4] for row in observations})
    splits = {"TRAIN": lambda date: date < "20250101",
              "CAL": lambda date: "20250101" <= date < "20260101",
              "TEST": lambda date: "20260101" <= date < "20270101"}
    return {"schema": "pace-lap-recovery-report-v1", "candidate_count": candidate_count,
            "processed": candidate_count, "verified_races": len(observations),
            "exact600_labels": sum(row["first600_seconds"] is not None for row in observations),
            "excluded": sum(exclusions.values()), "excluded_reasons": dict(sorted(exclusions.items())),
            "coverage_by_year": {year: _coverage([row for row in observations if row["race_date"].startswith(year)])
                                 for year in years},
            "coverage_by_split": {split: _coverage([row for row in observations if select(row["race_date"])])
                                  for split, select in splits.items()},
            "verified_races_by_date": dict(sorted(Counter(row["race_date"] for row in observations).items())),
            "exact600_labels_by_date": dict(sorted(Counter(row["race_date"] for row in observations
                                                          if row["first600_seconds"] is not None).items())),
            "original_saved_lap_dictionaries_used": False,
            "source_identity_policy": "canonical/og:url or inline result-laptime render call plus matching DOM target; never navigation/URL alone",
            "historical_capture_time_proven": False, "network_used": False, "sqlite_writes": False,
            "training_or_activation_performed": False}


def _readonly(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    return connection


def training_observations(observations):
    """Export only exact600 records from this tool's independently verified audit.

    This is an export gate, not a source validator for arbitrary external records.
    The flags must originate in recover_observation; they are never source proof
    by themselves. Pre-export audit records need not carry the final validated
    field: it is derived from the completed validation and measured boundary.
    """
    required = ("http_status_200", "transport_identity_match", "page_identity_verified",
                "displayed_date_verified", "expected_metadata_match", "sectional_row_observed",
                "winner_finish_match", "exact600_boundary")
    labels = []
    for row in observations:
        value = row.get("first600_seconds")
        validation = row.get("validation", {})
        if (row.get("validated") is False or isinstance(value, bool)
                or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0
                or not all(validation.get(key) is True for key in required)):
            continue
        labels.append({**row, "validated": True})
    return labels


def write_recovery_outputs(observations, report, output_dir):
    """Save compact training labels and the richer aggregate audit; never HTML."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    observation_file = directory / "recovered-laps.json"
    labels = training_observations(observations)
    observation_file.write_text(json.dumps({"schema": SCHEMA, "observations": labels},
                                          ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    report["exported_exact600_labels"] = len(labels)
    report["observations_file_sha256"] = hashlib.sha256(observation_file.read_bytes()).hexdigest()
    (directory / "recovery-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def recover_cache(cache_db, race_db, output_dir, *, max_candidates=MAX_CANDIDATES):
    """Inspect at most 3,235 existing candidates, sequentially, in read snapshots."""
    if not 0 < max_candidates <= MAX_CANDIDATES:
        raise ValueError("candidate_bound_must_be_between_1_and_3235")
    pattern = PREFIX + "[0-9]" * 12
    where = """normalized_url >= ? AND normalized_url < ?
               AND (normalized_url GLOB ? OR normalized_url GLOB ?)
               AND substr(normalized_url, ?, 2) BETWEEN '01' AND '10'"""
    parameters = (PREFIX, PREFIX + "\uffff", pattern, pattern + "/", len(PREFIX) + 5)
    observations, exclusions = [], Counter()
    rejected_years = {}
    with closing(_readonly(cache_db)) as cache, closing(_readonly(race_db)) as races:
        count = cache.execute("SELECT COUNT(*) FROM http_cache WHERE " + where, parameters).fetchone()[0]
        if count > max_candidates:
            raise ValueError(f"candidate_bound_exceeded:{count}>{max_candidates}")
        candidates = cache.execute(
            "SELECT normalized_url,final_url,status FROM http_cache WHERE " + where
            + " ORDER BY normalized_url LIMIT ?", (*parameters, max_candidates)).fetchall()
        seen = set()
        for source_url, final_url, status in candidates:
            race_id = _url_race_id(source_url)
            if race_id in seen:
                reason = "duplicate_candidate_race_id"
                observation = None
            else:
                seen.add(race_id)
                meta = races.execute("SELECT data FROM races_ultimate WHERE race_id=?", (race_id,)).fetchone()
                expected = json.loads(meta[0]) if meta else None
                row = cache.execute("SELECT body FROM http_cache WHERE normalized_url=?", (source_url,)).fetchone()
                observation, reason = recover_observation(
                    row[0], source_url=source_url, final_url=final_url, http_status=status, expected=expected)
            if reason:
                exclusions[reason] += 1
                rejected_years.setdefault(race_id[:4], Counter())[reason] += 1
            else:
                observations.append(observation)
    observations.sort(key=lambda row: (row["race_date"], row["race_id"]))
    report = aggregate_recovery(observations, exclusions, candidate_count=count)
    report["excluded_reasons_by_candidate_year"] = {
        year: dict(sorted(reasons.items())) for year, reasons in sorted(rejected_years.items())}
    report["created_at_utc"] = datetime.now(timezone.utc).isoformat()
    write_recovery_outputs(observations, report, output_dir)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-db", type=Path, default=ROOT / "keiba" / "data" / "fetch_cache.db")
    parser.add_argument("--race-db", type=Path, default=ROOT / "keiba" / "data" / "keiba_ultimate.db")
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "reports" / "generated" / "pace-stack" / "20261114-v1")
    parser.add_argument("--max-candidates", type=int, default=MAX_CANDIDATES)
    args = parser.parse_args()
    report = recover_cache(args.cache_db, args.race_db, args.output_dir, max_candidates=args.max_candidates)
    print(json.dumps({key: report[key] for key in ("candidate_count", "verified_races", "exact600_labels",
                                                  "excluded_reasons", "coverage_by_year", "coverage_by_split")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
