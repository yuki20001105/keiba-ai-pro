"""Independent, strict final-3F source evidence without fetching or DB writes.

Only an explicitly labelled result clock is accepted. A source observation made
today is not historical publication evidence, even when its race date is old.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
import hashlib
import math
import re
import unicodedata
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

VERSION = "last3f-source-evidence-v1"
# 後3F was verified against three original public result responses on 2026-10-17.
CLOCK_HEADERS = frozenset({"上り", "上がり", "上り3F", "上がり3F", "後3F"})
MISSING_TOKENS = frozenset({"", "-", "--", "---", "未計測", "計不"})


def _text(value: object) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")))


def clock_column(headers: Sequence[str]) -> int:
    """Return the unique exact clock column; never match an index substring."""
    hits = [index for index, header in enumerate(headers) if _text(header) in CLOCK_HEADERS]
    return hits[0] if len(hits) == 1 else -1


def classify_last3f(value: object) -> tuple[str, float | None]:
    """Retain missing/masked/invalid distinctions; never invent zero clocks."""
    if value is None:
        return "missing", None
    if isinstance(value, bool):
        return "invalid", None
    raw = re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value)))
    if raw in MISSING_TOKENS:
        return "missing", None
    if raw and set(raw) == {"*"}:
        return "masked", None
    if not re.fullmatch(r"\d+(?:\.\d+)?", raw):
        return "invalid", None
    number = float(raw)
    # The existing current-runner audit uses this same final-3F domain.
    if not math.isfinite(number) or not 10 < number < 100:
        return "invalid", None
    return "valid", number


def _day(value: object) -> str:
    raw = _text(value).replace("-", "").replace("/", "")
    if not re.fullmatch(r"\d{8}", raw):
        raise ValueError("last3f-invalid-expected-date")
    try:
        datetime.strptime(raw, "%Y%m%d")
    except ValueError as exc:
        raise ValueError("last3f-invalid-expected-date") from exc
    return raw


def _url_race_id(url: str) -> str:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in {"db.netkeiba.com", "race.netkeiba.com"}
            or parsed.username or parsed.password or parsed.fragment or parsed.port not in (None, 443)):
        raise ValueError("last3f-source-url-not-allowlisted")
    if parsed.hostname == "db.netkeiba.com":
        match = re.fullmatch(r"/race/(\d{12})/?", parsed.path)
        if not match or parsed.query:
            raise ValueError("last3f-source-url-not-result-page")
        return match[1]
    query = parse_qs(parsed.query, keep_blank_values=True)
    ids = query.get("race_id", [])
    if parsed.path != "/race/result.html" or len(ids) != 1 or not re.fullmatch(r"\d{12}", ids[0]):
        raise ValueError("last3f-source-url-not-result-page")
    # No cookies, tokens or arbitrary query-bearing source identities are admitted.
    if any(key not in {"race_id", "rf"} or len(values) != 1 for key, values in query.items()):
        raise ValueError("last3f-source-url-query-not-allowlisted")
    return ids[0]


def _observed_at(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("last3f-observation-timezone-required")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("last3f-observation-timezone-required") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("last3f-observation-timezone-required")
    return value


def _identity(item: dict) -> tuple[int, str, str]:
    raw = item.get("horse_number")
    if isinstance(raw, bool) or not re.fullmatch(r"\d{1,2}", _text(raw)) or not 1 <= int(raw) <= 18:
        raise ValueError("last3f-expected-runner-number-invalid")
    hid, name = _text(item.get("horse_id")), _text(item.get("horse_name"))
    if not re.fullmatch(r"[A-Za-z0-9]+", hid) or not name:
        raise ValueError("last3f-expected-runner-identity-missing")
    return int(raw), hid, name


def _cache_identity_receipt(
    receipt: dict | None, *, source_url: str, observed_at: str,
    raw_sha256: str, raw_body: bytes | None,
) -> dict:
    """Verify explicit legacy-cache transport evidence, not publisher canonical."""
    if not isinstance(receipt, dict) or raw_body is None:
        raise ValueError("last3f-cached-transport-receipt-and-bytes-required")
    if (receipt.get("normalized_url") != source_url or receipt.get("status") != 200
            or isinstance(receipt.get("status"), bool) or receipt.get("body_sha256") != raw_sha256):
        raise ValueError("last3f-cached-transport-url-status-digest-mismatch")
    timestamp = receipt.get("fetched_at")
    if (isinstance(timestamp, bool) or not isinstance(timestamp, (int, float))
            or not math.isfinite(timestamp) or timestamp <= 0):
        raise ValueError("last3f-cached-transport-timestamp-invalid")
    if abs(datetime.fromisoformat(observed_at.replace("Z", "+00:00")).timestamp() - timestamp) > 1e-5:
        raise ValueError("last3f-cached-transport-observation-time-mismatch")
    return {"normalized_url": source_url, "status": 200, "body_sha256": raw_sha256,
            "fetched_at": timestamp, "raw_bytes_digest_verified": True}


def _visible_metadata(soup: BeautifulSoup) -> tuple[str, int | None]:
    nodes = list(soup.select("p.smalltxt,div.smalltxt,.RaceData02,.RaceHeader_Value"))
    if soup.title:
        nodes.append(soup.title)
    days, counts = set(), set()
    for node in nodes:
        value = unicodedata.normalize("NFKC", node.get_text(" ", strip=True))
        for year, month, day in re.findall(r"(\d{4})[年/-]\s*(\d{1,2})[月/-]\s*(\d{1,2})(?:日|\b)", value):
            days.add(_day(f"{int(year):04d}{int(month):02d}{int(day):02d}"))
        for match in re.finditer(r"(\d{1,2})\s*頭", value):
            before = re.sub(r"\s+", "", value[:match.start()])
            if not before.endswith("フルゲート"):
                counts.add(int(match[1]))
    if len(days) != 1:
        raise ValueError("last3f-visible-source-date-not-unique")
    if len(counts) > 1:
        raise ValueError("last3f-declared-count-conflict")
    return next(iter(days)), next(iter(counts)) if counts else None


def parse_last3f_evidence(
    html: str,
    *,
    race_id: str,
    source_url: str,
    observed_at_utc: str,
    raw_body_sha256: str,
    expected_date: str,
    expected_runners: Sequence[dict],
    http_status: int = 200,
    raw_body: bytes | None = None,
    allow_cached_transport_identity: bool = False,
    cache_receipt: dict | None = None,
) -> dict:
    """Bind a whole result table to exact caller-supplied race/runner identities.

    The caller must hash the original bytes, and supply its real response/cache
    receipt timestamp (not the race date). Passing ``raw_body`` additionally
    verifies that hash here. This parser never asserts historical PIT capture.
    Legacy cached pages without publisher canonical tags require the separate,
    explicit ``allow_cached_transport_identity`` option plus exact transport
    receipt, original bytes, visible date and all runner identities. A present
    contradictory canonical is always rejected, including in this option.
    """
    if (http_status != 200 or not isinstance(html, str) or not html
            or not re.fullmatch(r"\d{12}", str(race_id)) or not 1 <= int(race_id[4:6]) <= 10):
        raise ValueError("last3f-not-a-jra-successful-source")
    if _url_race_id(source_url) != race_id:
        raise ValueError("last3f-request-race-id-mismatch")
    observed_at_utc = _observed_at(observed_at_utc)
    if not isinstance(raw_body_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", raw_body_sha256):
        raise ValueError("last3f-raw-body-sha256-required")
    if raw_body is not None and (not isinstance(raw_body, bytes) or hashlib.sha256(raw_body).hexdigest() != raw_body_sha256):
        raise ValueError("last3f-raw-body-sha256-mismatch")
    expected_date = _day(expected_date)
    if expected_date[:4] != race_id[:4]:
        raise ValueError("last3f-race-year-mismatch")
    expected = [_identity(item) for item in expected_runners]
    if (not expected or len({row[0] for row in expected}) != len(expected)
            or len({row[1] for row in expected}) != len(expected)):
        raise ValueError("last3f-expected-identities-not-unique")
    soup = BeautifulSoup(html, "lxml")
    canonical_urls = [str(tag.get("href") or tag.get("content") or "")
                      for tag in soup.select('link[rel="canonical"],meta[property="og:url"]')]
    cached_transport = None
    if not canonical_urls:
        if allow_cached_transport_identity is not True:
            raise ValueError("last3f-canonical-race-url-missing")
        cached_transport = _cache_identity_receipt(
            cache_receipt, source_url=source_url, observed_at=observed_at_utc,
            raw_sha256=raw_body_sha256, raw_body=raw_body,
        )
    if any(_url_race_id(url) != race_id for url in canonical_urls):
        raise ValueError("last3f-canonical-race-id-mismatch")
    race_date, declared_count = _visible_metadata(soup)
    if race_date != expected_date:
        raise ValueError("last3f-visible-date-mismatch")
    candidates = []
    for table in soup.select("table.race_table_01,table.RaceTable01,table.ResultsByRaceDetail"):
        trs = table.find_all("tr")
        if not trs:
            continue
        headers = [_text(cell.get_text(" ", strip=True)) for cell in trs[0].find_all(["th", "td"])]
        if headers.count("馬番") == 1 and headers.count("馬名") == 1:
            candidates.append((trs, headers))
    if len(candidates) != 1:
        raise ValueError("last3f-result-table-not-unique")
    trs, headers = candidates[0]
    cc = clock_column(headers)
    if cc == -1:
        raise ValueError("last3f-clock-header-missing-or-ambiguous")
    nc, hc = headers.index("馬番"), headers.index("馬名")
    rows = []
    for tr in trs[1:]:
        cells = tr.find_all("td")
        if len(cells) <= max(nc, hc):
            continue
        number = _text(cells[nc].get_text(strip=True))
        if not re.fullmatch(r"\d{1,2}", number) or not 1 <= int(number) <= 18:
            raise ValueError("last3f-source-runner-number-invalid")
        links = []
        for anchor in cells[hc].find_all("a", href=True):
            match = re.fullmatch(r"/horse/(?:result/(?:recent/)?)?([A-Za-z0-9]+)/?", urlsplit(anchor["href"]).path)
            if match:
                links.append((match[1], _text(anchor.get_text(strip=True))))
        if len(links) != 1 or not links[0][1]:
            raise ValueError("last3f-source-horse-identity-not-unique")
        raw = cells[cc].get_text(strip=True) if len(cells) > cc else None
        state, seconds = classify_last3f(raw)
        rows.append({"horse_number": int(number), "horse_id": links[0][0], "horse_name": links[0][1],
                     "last3f_state": state, "last3f_seconds": seconds, "source_raw": raw})
    actual = [_identity(item) for item in rows]
    if (len({row[0] for row in actual}) != len(actual) or len({row[1] for row in actual}) != len(actual)
            or sorted(actual) != sorted(expected)):
        raise ValueError("last3f-all-runner-identity-mismatch")
    if declared_count is not None and declared_count != len(rows):
        raise ValueError("last3f-declared-runner-count-mismatch")
    return {"schema": VERSION, "race_id": race_id, "race_date": race_date,
            "runner_count": len(rows), "declared_runner_count": declared_count,
            "declared_runner_count_available": declared_count is not None,
            "clock_header": headers[cc], "clock_column": cc, "rows": rows,
            "source": {"source_url": source_url, "http_status": http_status,
                       "raw_body_sha256": raw_body_sha256, "decoded_html_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
                       "received_at_utc": observed_at_utc, "observed_at_utc": observed_at_utc,
                       "raw_digest_verification": "verified_against_bytes" if raw_body is not None else "caller_digest_supplied",
                       "canonical_urls": canonical_urls, "visible_race_date_verified": True,
                       "all_runner_identity_verified": True, "historical_capture_time_proven": False,
                       "canonical_identity_verified": bool(canonical_urls),
                       "identity_grade": "cached_transport_url_plus_visible_date_and_all_runners" if cached_transport else "canonical_url_plus_visible_date_and_all_runners",
                       "cached_transport_receipt": cached_transport},
            "missing_policy": "masked/missing/invalid are distinct; no zero fill", "clock_domain_seconds": "10 < value < 100"}
