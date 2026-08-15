"""Controlled ingestion of public JRA annual-result PDF extracts.

The official PDFs are an outcome source, not a point-in-time market source.
Final odds printed in a result PDF are therefore stored outside the training
payload and must never satisfy the Phase3N point-in-time-odds contract.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo


INDEX_SCHEMA = "jra-official-results-source-index-v1"
MANIFEST_SCHEMA = "jra-official-results-manifest-v1"
RECORD_SCHEMA = "jra-official-result-runner-v1"
JRA_REPORT_ROOT = "https://www.jra.go.jp/datafile/seiseki/report/"
JRA_TERMS_URL = "https://www.jra.go.jp/use/"
APPROVED_HOST = "www.jra.go.jp"
VENUE_CODES = {
    "札幌": "01",
    "函館": "02",
    "福島": "03",
    "新潟": "04",
    "東京": "05",
    "中山": "06",
    "中京": "07",
    "京都": "08",
    "阪神": "09",
    "小倉": "10",
}
REQUIRED_PAYLOAD_FIELDS = frozenset(
    {"distance", "surface", "finish", "time_seconds", "horse_number"}
)


@dataclass(frozen=True)
class PreparedOfficialResultsImport:
    manifest: dict[str, Any]
    manifest_sha256: str
    records: tuple[dict[str, Any], ...]
    coverage_start: str
    coverage_end: str


class IncompleteTimeGlyphError(ValueError):
    """A printed tenth-of-second glyph could not be extracted reliably."""


class UnsupportedRunnerLayoutError(ValueError):
    """A race table layout cannot be mapped without guessing outcomes."""


class UnrecoverableRaceMetadataError(ValueError):
    """A printed race header cannot be recovered deterministically."""


class InsufficientTimedFinishersError(ValueError):
    """A race table cannot yield a minimally reliable timed field."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _approved_jra_url(url: str, *, suffix: str | None = None) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != APPROVED_HOST:
        raise ValueError(f"source URL is outside the approved JRA host: {url!r}")
    if not parsed.path.startswith("/datafile/seiseki/report/"):
        raise ValueError(f"source URL is outside the annual-result path: {url!r}")
    if suffix and not parsed.path.lower().endswith(suffix):
        raise ValueError(f"source URL does not end with {suffix}: {url!r}")
    return url


def _relative_file(root: Path, relative: str) -> Path:
    if not relative or Path(relative).is_absolute():
        raise ValueError("manifest paths must be non-empty and relative")
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("manifest path escapes its bundle directory") from exc
    if not resolved.is_file():
        raise ValueError(f"manifest file does not exist: {relative}")
    return resolved


def discover_source_index(
    years: Iterable[int],
    *,
    get: Callable[..., Any] | None = None,
    fetched_at: str | None = None,
) -> dict[str, Any]:
    """Discover official annual PDF links without downloading PDF bytes."""

    if get is None:
        import requests

        get = requests.get
    from bs4 import BeautifulSoup

    requested = sorted({int(year) for year in years})
    if not requested or requested[0] < 2002:
        raise ValueError("JRA annual PDFs are indexed from 2002 onward")
    documents: list[dict[str, Any]] = []
    index_pages: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for year in requested:
        index_url = _approved_jra_url(f"{JRA_REPORT_ROOT}{year}.html", suffix=".html")
        response = get(index_url, timeout=30)
        response.raise_for_status()
        index_pages.append({"year": str(year), "url": index_url})
        encoding = getattr(response, "apparent_encoding", None) or getattr(
            response, "encoding", None
        ) or "utf-8"
        soup = BeautifulSoup(response.content.decode(encoding, errors="replace"), "lxml")
        for anchor in soup.select('a[href$=".pdf"], a[href$=".PDF"]'):
            url = _approved_jra_url(urljoin(index_url, anchor.get("href", "")), suffix=".pdf")
            if url in seen_urls:
                continue
            seen_urls.add(url)
            label = " ".join(anchor.get_text(" ", strip=True).split())
            documents.append(
                {
                    "year": year,
                    "url": url,
                    "label": label,
                    "filename": Path(urlparse(url).path).name,
                }
            )
    if not documents:
        raise ValueError("no annual-result PDFs were discovered")
    return {
        "schema": INDEX_SCHEMA,
        "provider": "JRA official annual results",
        "years": requested,
        "fetched_at": fetched_at or datetime.now(timezone.utc).isoformat(),
        "terms_url": JRA_TERMS_URL,
        "purpose": "internal-research-speed-deviation-outcomes",
        "index_pages": index_pages,
        "documents": documents,
    }


def download_source_index(
    index: dict[str, Any],
    destination: Path,
    *,
    get: Callable[..., Any] | None = None,
    delay_seconds: float = 1.0,
    max_files: int | None = None,
    workers: int = 4,
) -> dict[str, Any]:
    """Download indexed PDFs with bounded rate and record byte provenance."""

    if get is None:
        import requests

        get = requests.get
    if index.get("schema") != INDEX_SCHEMA:
        raise ValueError(f"source index schema must be {INDEX_SCHEMA}")
    if delay_seconds < 0.5:
        raise ValueError("delay_seconds must be at least 0.5")
    if not 1 <= workers <= 8:
        raise ValueError("workers must be between 1 and 8")
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    documents = index.get("documents")
    if not isinstance(documents, list) or not documents:
        raise ValueError("source index contains no documents")
    selected = documents if max_files is None else documents[: max(max_files, 0)]
    downloaded_by_url: dict[str, dict[str, Any]] = {}
    pending: list[tuple[dict[str, Any], Path, Path]] = []
    for item in selected:
        year = int(item["year"])
        url = _approved_jra_url(str(item["url"]), suffix=".pdf")
        filename = Path(str(item["filename"])).name
        relative = Path(str(year)) / filename
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            with target.open("rb") as handle:
                if handle.read(5) != b"%PDF-":
                    raise ValueError(f"existing source is not a PDF: {target}")
            downloaded_by_url[url] = {
                **item,
                "path": relative.as_posix(),
                "sha256": sha256_file(target),
                "bytes": target.stat().st_size,
                "content_type": "application/pdf",
                "downloaded_at": datetime.now(timezone.utc).isoformat(),
                "reused_existing_bytes": True,
            }
            continue
        pending.append((item, relative, target))

    rate_lock = threading.Lock()
    next_request_at = [time.monotonic()]

    def fetch_one(task: tuple[dict[str, Any], Path, Path]) -> dict[str, Any]:
        item, relative, target = task
        url = str(item["url"])
        with rate_lock:
            wait = next_request_at[0] - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            next_request_at[0] = time.monotonic() + delay_seconds
        response = get(url, timeout=60)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";", 1)[0]
        if content_type != "application/pdf" or not response.content.startswith(b"%PDF-"):
            raise ValueError(f"source did not return a PDF: {url}")
        partial = target.with_suffix(target.suffix + ".partial")
        partial.write_bytes(response.content)
        partial.replace(target)
        return {
            **item,
            "path": relative.as_posix(),
            "sha256": sha256_file(target),
            "bytes": target.stat().st_size,
            "content_type": content_type,
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
            "reused_existing_bytes": False,
        }

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch_one, task): str(task[0]["url"]) for task in pending}
        for future in as_completed(futures):
            result = future.result()
            downloaded_by_url[str(result["url"])] = result
    downloaded = [downloaded_by_url[str(item["url"])] for item in selected]
    return {
        **index,
        "download_root": str(destination),
        "downloaded_document_count": len(downloaded),
        "download_complete": len(downloaded) == len(documents),
        "downloaded_documents": downloaded,
    }


_CID_DIGIT = re.compile(r"\(cid:(987[2-9]|988[01])\)")
_CID_OTHER = re.compile(r"\(cid:\d+\)")


def normalize_jra_pdf_text(value: str | None) -> str:
    """Normalize JRA embedded-font digit CIDs without guessing other glyphs."""

    text = value or ""
    text = _CID_DIGIT.sub(lambda match: str(int(match.group(1)) - 9872), text)
    text = _CID_OTHER.sub("", text)
    return (
        text.replace("：", ":")
        .replace("．", ".")
        .replace("，", ",")
        .replace("−", "-")
        .replace("―", "-")
    )


def _cell_lines(value: str | None) -> list[str]:
    return [normalize_jra_pdf_text(line).strip() for line in (value or "").splitlines() if line.strip()]


def _without_affiliation_lines(lines: list[str]) -> list[str]:
    return [
        line
        for line in lines
        if not re.fullmatch(r"(?:（[^）]+）|\([^)]*\))", line.replace(" ", ""))
    ]


def _time_seconds(value: str, previous: float | None) -> float:
    if value.strip() == "〃":
        if previous is None:
            raise ValueError("ditto time appears before an explicit time")
        return previous
    normalized = normalize_jra_pdf_text(value).replace(" ", "")
    misplaced_decimal = re.fullmatch(r"(\d+):(\d{2})(\d)\.", normalized)
    if misplaced_decimal:
        return int(misplaced_decimal.group(1)) * 60.0 + float(
            f"{misplaced_decimal.group(2)}.{misplaced_decimal.group(3)}"
        )
    misplaced_short_decimal = re.fullmatch(r"(\d{2})(\d)\.", normalized)
    if misplaced_short_decimal:
        return float(
            f"{misplaced_short_decimal.group(1)}.{misplaced_short_decimal.group(2)}"
        )
    if re.fullmatch(r"(?:\d+:)?\d{2}\.", normalized):
        raise IncompleteTimeGlyphError(
            f"printed tenth-of-second glyph is unavailable: {value!r}"
        )
    if re.fullmatch(r"\d{1,2}\.\d", normalized):
        return float(normalized)
    match = re.fullmatch(r"(\d+):(\d{2}\.\d)", normalized)
    if not match:
        raise ValueError(f"unsupported official result time: {value!r}")
    return int(match.group(1)) * 60.0 + float(match.group(2))


def _result_odds(value: str | None) -> float | None:
    if value is None:
        return None
    normalized = normalize_jra_pdf_text(value).replace(" ", "")
    match = re.search(r"\d+\.\d", normalized)
    if not match:
        misplaced_decimal = re.search(r"(\d+)(\d)\.", normalized)
        if misplaced_decimal:
            return float(
                f"{misplaced_decimal.group(1)}.{misplaced_decimal.group(2)}"
            )
    if not match:
        return None
    return float(match.group(0))


def _horse_identity(name: str) -> tuple[str, str]:
    normalized = "".join(name.split())
    if not normalized:
        raise ValueError("official result row has an empty horse name")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    return normalized, f"jra-name-{digest}"


def _parse_crop_metadata(text: str, *, source_year: int | None = None) -> dict[str, Any]:
    normalized = normalize_jra_pdf_text(text)
    prefix = normalized.split("負担重量", 1)[0]
    month_day = re.search(r"(\d{1,2})月(\d{1,2})日", prefix)
    explicit_venue = re.search(r"（(\d{4})年\d+([^）]+)）", prefix)
    compact_venue = re.search(r"（\d+([^\d（）]+)\d*）", prefix)
    race = re.search(r"第\s*(\d+)\s*競走", prefix)
    post = re.search(r"発走\s*(\d{1,2})時\s*(\d{1,2})分", prefix)
    distances = re.findall(r"(?<!\d)(\d{1,2})[,](\d{3})(?!\d)", prefix)
    conditions = re.search(
        r"\d{1,2}月\d{1,2}日\s*(晴|曇|雨|小雨|雪|小雪)\s*(良|稍重|重|不良)",
        prefix,
    )
    race_name_match = re.search(
        r"第\s*\d+\s*競走\s+(.+?)\s+\d{1,2},\d{3}", prefix, re.S
    )
    if not month_day or not race or not post or not distances:
        raise UnrecoverableRaceMetadataError("official PDF crop lacks required race metadata")
    if explicit_venue:
        year, venue_raw = explicit_venue.groups()
    elif compact_venue and source_year is not None:
        year, venue_raw = str(source_year), compact_venue.group(1)
    else:
        raise UnrecoverableRaceMetadataError(
            "official PDF crop lacks a recoverable year/venue"
        )
    month, day = month_day.groups()
    venue = next((name for name in VENUE_CODES if name in venue_raw), None)
    if venue is None:
        raise UnrecoverableRaceMetadataError(
            f"unsupported JRA venue in official PDF: {venue_raw!r}"
        )
    if (
        "障害" in prefix
        or "ジャンプ" in prefix
        or re.search(r"（\s*Ｊ\s*[・.]?\s*Ｇ", prefix)
    ):
        surface = "jump"
    elif re.search(r"[（(]\s*芝\s*(?:[・][^）)]*)?[）)]", prefix):
        surface = "turf"
    elif re.search(r"[（(]\s*ダート\s*(?:[・][^）)]*)?[）)]", prefix):
        surface = "dirt"
    else:
        raise UnrecoverableRaceMetadataError("official PDF crop lacks a supported surface")
    race_date = datetime(int(year), int(month), int(day))
    race_number = int(race.group(1))
    race_id = f"{race_date:%Y%m%d}{VENUE_CODES[venue]}{race_number:02d}"
    hour, minute = (int(value) for value in post.groups())
    # JRA publication times are Japan Standard Time (UTC+09:00).
    post_time = datetime(
        race_date.year,
        race_date.month,
        race_date.day,
        hour,
        minute,
        tzinfo=ZoneInfo("Asia/Tokyo"),
    ).astimezone(timezone.utc)
    distance = int("".join(distances[-1]))
    return {
        "race_id": race_id,
        "race_date": race_date.date().isoformat(),
        "post_time": post_time.isoformat(),
        "venue": venue,
        "race_number": race_number,
        "distance": distance,
        "surface": surface,
        "weather": conditions.group(1) if conditions else None,
        "field_condition": conditions.group(2) if conditions else None,
        "race_name": " ".join(race_name_match.group(1).split()) if race_name_match else None,
    }


def parse_official_result_crop(
    crop: Any,
    *,
    source_url: str,
    source_pdf_sha256: str,
    page_number: int,
    column: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Parse one visually isolated half-page and fail closed on column drift."""

    text = crop.extract_text(x_tolerance=2, y_tolerance=2) or ""
    source_year_match = re.search(r"/report/(\d{4})/", source_url)
    metadata = _parse_crop_metadata(
        text,
        source_year=int(source_year_match.group(1)) if source_year_match else None,
    )
    if metadata["surface"] == "jump":
        return [], {"race_id": metadata["race_id"], "status": "excluded-jump-race"}
    chunks: list[dict[str, list[str]]] = []
    for table in crop.extract_tables() or []:
        for row in table:
            if len(row) < 23 or not row[2] or "馬 名" in row[2] or "（" in row[2] and "頭）" in row[2]:
                continue
            if len(row) > 25:
                raise UnsupportedRunnerLayoutError(
                    f"{metadata['race_id']} page {page_number} {column}: "
                    f"unsupported runner table width {len(row)}"
                )
            names = _without_affiliation_lines(_cell_lines(row[2]))
            numbers = _cell_lines(row[1])
            odds = _cell_lines(row[-1])
            time_index = None
            for candidate_index in range(len(row) - 2, max(len(row) - 7, -1), -1):
                candidate = _cell_lines(row[candidate_index])
                if len(candidate) != len(names):
                    continue
                if all(
                    value == "〃"
                    or re.search(r"競走中止|中止|失格|除外", value)
                    or re.fullmatch(r"(?:\d+:)?\d{1,2}(?:\.\d|\d\.|\.)", value.replace(" ", ""))
                    for value in candidate
                ):
                    time_index = candidate_index
                    break
            if time_index is None:
                raise UnsupportedRunnerLayoutError(
                    f"{metadata['race_id']} page {page_number} {column}: "
                    "runner time column is not identifiable"
                )
            times = _cell_lines(row[time_index])
            time_prefixes: list[str] = []
            for candidate_index in range(time_index - 1, max(time_index - 4, -1), -1):
                candidate = _cell_lines(row[candidate_index])
                if len(candidate) == len(names) and any(":" in value for value in candidate):
                    time_prefixes = candidate
                    break
            if not names:
                continue
            if not (len(names) == len(numbers) == len(times)):
                raise UnsupportedRunnerLayoutError(
                    f"{metadata['race_id']} page {page_number} {column}: "
                    "runner table columns have different row counts"
                )
            weight_index = None
            for candidate_index in range(6, min(9, len(row))):
                candidate = _cell_lines(row[candidate_index])
                if len(candidate) == len(names) and all(
                    re.fullmatch(r"\d{2}(?:\.\d)?", value.replace(" ", ""))
                    for value in candidate
                ):
                    weight_index = candidate_index
                    break
            optional = {
                "frames": _cell_lines(row[0]),
                "sex_age": _cell_lines(row[5]),
                "weights": _cell_lines(row[weight_index]) if weight_index is not None else [],
                "jockeys": (
                    _without_affiliation_lines(_cell_lines(row[weight_index + 2]))
                    if weight_index is not None and weight_index + 2 < len(row)
                    else []
                ),
                "trainers": (
                    _cell_lines(row[weight_index + 6])
                    if weight_index is not None and weight_index + 6 < len(row)
                    else []
                ),
                "bodyweights": (
                    _cell_lines(row[weight_index + 11])
                    if weight_index is not None and weight_index + 11 < len(row)
                    else []
                ),
            }
            chunks.append(
                {
                    "names": names,
                    "numbers": numbers,
                    "times": times,
                    "time_prefixes": time_prefixes,
                    "odds": odds if len(odds) == len(names) else [None] * len(names),
                    **{
                        key: value if len(value) == len(names) else []
                        for key, value in optional.items()
                    },
                }
            )
    if not chunks:
        raise UnsupportedRunnerLayoutError(
            f"{metadata['race_id']} has no parseable runner table"
        )
    records: list[dict[str, Any]] = []
    previous_time: float | None = None
    finish = 0
    non_timed_runner_count = 0
    seen_numbers: set[int] = set()
    for chunk in chunks:
        names = chunk["names"]
        numbers = chunk["numbers"]
        times = chunk["times"]
        odds_values = chunk["odds"]
        for chunk_index, (name_raw, number_raw, time_raw, odds_raw) in enumerate(
            zip(names, numbers, times, odds_values)
        ):
            horse_name, horse_id = _horse_identity(name_raw)
            number_match = re.fullmatch(r"\d{1,2}", number_raw.replace(" ", ""))
            if not number_match:
                raise ValueError(f"{metadata['race_id']} has invalid horse number: {number_raw!r}")
            horse_number = int(number_match.group(0))
            if horse_number in seen_numbers:
                raise ValueError(f"{metadata['race_id']} contains duplicate horse numbers")
            seen_numbers.add(horse_number)
            if re.search(r"競走中止|中止|失格|除外", normalize_jra_pdf_text(time_raw)):
                non_timed_runner_count += 1
                continue
            if chunk["time_prefixes"] and re.fullmatch(r"\d\.\d", time_raw):
                prefix_match = re.search(r"(\d+):(\d)$", chunk["time_prefixes"][chunk_index])
                if prefix_match:
                    time_raw = (
                        f"{prefix_match.group(1)}:{prefix_match.group(2)}"
                        f"{time_raw}"
                    )
            time_seconds = _time_seconds(time_raw, previous_time)
            previous_time = time_seconds
            finish += 1
            result_odds = _result_odds(odds_raw)
            source_record_id = f"JRA-{metadata['race_id']}-{horse_number:02d}"
            payload: dict[str, Any] = {
                "race_id": metadata["race_id"],
                "horse_id": horse_id,
                "horse_name": horse_name,
                "horse_number": horse_number,
                "finish": finish,
                "finish_position": finish,
                "time_seconds": time_seconds,
                "distance": metadata["distance"],
                "surface": metadata["surface"],
                "track_type": metadata["surface"],
                "venue": metadata["venue"],
                "race_number": metadata["race_number"],
            }
            for metadata_field in ("weather", "field_condition", "race_name"):
                if metadata[metadata_field]:
                    payload[metadata_field] = metadata[metadata_field]
            if chunk["frames"]:
                frame_match = re.search(r"\d+", chunk["frames"][chunk_index])
                if frame_match:
                    payload["frame_number"] = int(frame_match.group(0))
            if chunk["sex_age"]:
                sex_age = normalize_jra_pdf_text(chunk["sex_age"][chunk_index]).replace(" ", "")
                sex_age_match = re.search(r"([牡牝騸せん]+)(\d+)", sex_age)
                if sex_age_match:
                    payload["sex"] = sex_age_match.group(1)
                    payload["age"] = int(sex_age_match.group(2))
            if chunk["weights"]:
                weight_match = re.fullmatch(r"\d{2}(?:\.\d)?", chunk["weights"][chunk_index].replace(" ", ""))
                if weight_match:
                    payload["carried_weight"] = float(weight_match.group(0))
            if chunk["jockeys"]:
                jockey_name = "".join(chunk["jockeys"][chunk_index].split()).lstrip("☆▲△")
                if jockey_name:
                    payload["jockey_name"] = jockey_name
                    payload["jockey_id"] = "jra-jockey-" + hashlib.sha256(
                        jockey_name.encode("utf-8")
                    ).hexdigest()[:20]
            if chunk["trainers"]:
                trainer_name = "".join(chunk["trainers"][chunk_index].split())
                if trainer_name:
                    payload["trainer_name"] = trainer_name
                    payload["trainer_id"] = "jra-trainer-" + hashlib.sha256(
                        trainer_name.encode("utf-8")
                    ).hexdigest()[:20]
            if chunk["bodyweights"]:
                bodyweight_match = re.search(r"\d{3}", chunk["bodyweights"][chunk_index])
                if bodyweight_match:
                    payload["horse_weight"] = int(bodyweight_match.group(0))
            records.append(
                {
                    "schema": RECORD_SCHEMA,
                    "source_record_id": source_record_id,
                    "source_pdf_sha256": source_pdf_sha256,
                    "source_url": source_url,
                    "source_page": page_number,
                    "source_column": column,
                    "race_id": metadata["race_id"],
                    "horse_id": horse_id,
                    "race_date": metadata["race_date"],
                    "post_time": metadata["post_time"],
                    "payload": payload,
                    "result_win_odds": result_odds,
                }
            )
    if len(records) < 5:
        raise InsufficientTimedFinishersError(
            f"{metadata['race_id']} has fewer than five timed finishers"
        )
    return records, {
        "race_id": metadata["race_id"],
        "status": "parsed",
        "runner_count": len(records),
        "non_timed_runner_count": non_timed_runner_count,
        "page": page_number,
        "column": column,
    }


def parse_official_result_pdf(
    pdf_path: Path,
    *,
    source_url: str,
    expected_sha256: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Parse a JRA result PDF after verifying its exact bytes."""

    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - environment-specific guard
        raise RuntimeError("pdfplumber is required for JRA official PDF conversion") from exc
    pdf_path = pdf_path.resolve()
    if not pdf_path.is_file():
        raise ValueError(f"official PDF does not exist: {pdf_path}")
    source_url = _approved_jra_url(source_url, suffix=".pdf")
    actual_sha256 = sha256_file(pdf_path)
    if expected_sha256 and actual_sha256 != expected_sha256.lower():
        raise ValueError(f"source PDF sha256 mismatch: {pdf_path.name}")
    records: list[dict[str, Any]] = []
    races: list[dict[str, Any]] = []
    with pdfplumber.open(pdf_path) as document:
        for page_number, page in enumerate(document.pages, start=1):
            halves = (
                ("left", (0, 0, page.width / 2, page.height)),
                ("right", (page.width / 2, 0, page.width, page.height)),
            )
            for column, box in halves:
                crop = page.crop(box)
                crop_text = normalize_jra_pdf_text(
                    crop.extract_text(x_tolerance=2, y_tolerance=2) or ""
                )
                # Annual reports can include a later "競走の中止" notice that
                # repeats race headings but has no result metadata.  Treat only
                # crops with all printed result-header fields as result pages.
                if not (
                    re.search(r"第\s*\d+\s*競走", crop_text)
                    and re.search(r"\d{1,2}月\d{1,2}日", crop_text)
                    and re.search(r"発走\s*\d{1,2}時\s*\d{1,2}分", crop_text)
                    and re.search(r"(?<!\d)\d{1,2},\d{3}(?!\d)", crop_text)
                ):
                    continue
                try:
                    parsed, race_report = parse_official_result_crop(
                        crop,
                        source_url=source_url,
                        source_pdf_sha256=actual_sha256,
                        page_number=page_number,
                        column=column,
                    )
                except (
                    IncompleteTimeGlyphError,
                    UnsupportedRunnerLayoutError,
                    UnrecoverableRaceMetadataError,
                    InsufficientTimedFinishersError,
                ) as exc:
                    source_year_match = re.search(r"/report/(\d{4})/", source_url)
                    parsed = []
                    if isinstance(exc, UnrecoverableRaceMetadataError):
                        status = "excluded-unrecoverable-race-metadata"
                        race_id = f"unresolved:{actual_sha256[:16]}:{page_number}:{column}"
                    else:
                        metadata = _parse_crop_metadata(
                            crop_text,
                            source_year=(
                                int(source_year_match.group(1))
                                if source_year_match
                                else None
                            ),
                        )
                        race_id = metadata["race_id"]
                        if isinstance(exc, IncompleteTimeGlyphError):
                            status = "excluded-incomplete-time-glyph"
                        elif isinstance(exc, UnsupportedRunnerLayoutError):
                            status = "excluded-unsupported-runner-layout"
                        else:
                            status = "excluded-insufficient-timed-finishers"
                    race_report = {
                        "race_id": race_id,
                        "status": status,
                        "reason": str(exc),
                        "page": page_number,
                        "column": column,
                    }
                records.extend(parsed)
                races.append(race_report)
    race_ids = [row["race_id"] for row in races]
    if len(race_ids) != len(set(race_ids)):
        raise ValueError(f"source PDF produced duplicate race ids: {pdf_path.name}")
    excluded_unsupported_source_encoding_count = int(not records and not races)
    return records, {
        "schema": "jra-official-pdf-conversion-report-v1",
        "source_url": source_url,
        "source_path": str(pdf_path),
        "source_pdf_sha256": actual_sha256,
        "race_count": sum(row["status"] == "parsed" for row in races),
        "excluded_jump_race_count": sum(
            row["status"] == "excluded-jump-race" for row in races
        ),
        "excluded_incomplete_time_glyph_race_count": sum(
            row["status"] == "excluded-incomplete-time-glyph" for row in races
        ),
        "excluded_unsupported_runner_layout_race_count": sum(
            row["status"] == "excluded-unsupported-runner-layout" for row in races
        ),
        "excluded_unrecoverable_race_metadata_count": sum(
            row["status"] == "excluded-unrecoverable-race-metadata" for row in races
        ),
        "excluded_insufficient_timed_finishers_race_count": sum(
            row["status"] == "excluded-insufficient-timed-finishers" for row in races
        ),
        "excluded_unsupported_source_encoding_count": (
            excluded_unsupported_source_encoding_count
        ),
        "runner_count": len(records),
        "races": races,
        "point_in_time_odds_included": False,
    }


def _validate_record(raw: Any, allowed_years: frozenset[int]) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("schema") != RECORD_SCHEMA:
        raise ValueError(f"every record must use schema {RECORD_SCHEMA}")
    required = {
        "source_record_id",
        "source_pdf_sha256",
        "source_url",
        "race_id",
        "horse_id",
        "race_date",
        "post_time",
        "payload",
        "result_win_odds",
    }
    missing = required - set(raw)
    if missing:
        raise ValueError("official result record is missing: " + ", ".join(sorted(missing)))
    race_id = str(raw["race_id"]).strip()
    if not re.fullmatch(r"\d{12}", race_id):
        raise ValueError("race_id must contain exactly 12 digits")
    race_date = datetime.fromisoformat(str(raw["race_date"])).date()
    if race_date.year not in allowed_years or race_id[:8] != race_date.strftime("%Y%m%d"):
        raise ValueError("race date is outside scope or disagrees with race_id")
    post_time = datetime.fromisoformat(str(raw["post_time"]).replace("Z", "+00:00"))
    if post_time.tzinfo is None:
        raise ValueError("post_time must be timezone-aware")
    source_url = _approved_jra_url(str(raw["source_url"]), suffix=".pdf")
    source_pdf_sha256 = str(raw["source_pdf_sha256"]).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", source_pdf_sha256):
        raise ValueError("source_pdf_sha256 must be lowercase SHA-256")
    payload = raw["payload"]
    if not isinstance(payload, dict) or not REQUIRED_PAYLOAD_FIELDS.issubset(payload):
        raise ValueError("payload lacks required settled outcome fields")
    numeric = {
        "distance": float(payload["distance"]),
        "finish": int(payload["finish"]),
        "time_seconds": float(payload["time_seconds"]),
        "horse_number": int(payload["horse_number"]),
    }
    if min(numeric.values()) <= 0 or payload["surface"] not in {"turf", "dirt"}:
        raise ValueError("payload contains an out-of-range settled value")
    odds = (
        float(raw["result_win_odds"])
        if raw["result_win_odds"] is not None
        else None
    )
    if odds is not None and odds < 1.0:
        raise ValueError("result_win_odds must be at least 1.0")
    horse_id = str(raw["horse_id"]).strip()
    source_record_id = str(raw["source_record_id"]).strip()
    if not horse_id or not source_record_id:
        raise ValueError("horse_id and source_record_id are required")
    canonical_payload = dict(payload)
    canonical_payload.update(
        {
            "race_id": race_id,
            "horse_id": horse_id,
            "race_date": race_date.strftime("%Y%m%d"),
            "date": race_date.strftime("%Y%m%d"),
            "post_time": post_time.astimezone(timezone.utc).isoformat(),
            "official_source_provider": "JRA official annual results",
            "official_source_record_id": source_record_id,
            "official_source_url": source_url,
            "official_source_pdf_sha256": source_pdf_sha256,
            "point_in_time_odds_available": False,
        }
    )
    if any(key in canonical_payload for key in ("odds", "odds_observed_at", "popularity")):
        raise ValueError("final odds/popularity must not enter the training payload")
    return {
        "source_record_id": source_record_id,
        "source_pdf_sha256": source_pdf_sha256,
        "source_url": source_url,
        "race_id": race_id,
        "horse_id": horse_id,
        "race_date": race_date.isoformat(),
        "post_time": post_time.astimezone(timezone.utc).isoformat(),
        "payload": canonical_payload,
        "payload_sha256": _canonical_sha256(canonical_payload),
        "result_win_odds": odds,
    }


def prepare_official_results_import(
    manifest_path: Path,
    *,
    allowed_years: Iterable[int] = range(2019, 2025),
) -> PreparedOfficialResultsImport:
    manifest_path = manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema") != MANIFEST_SCHEMA:
        raise ValueError(f"manifest schema must be {MANIFEST_SCHEMA}")
    if manifest.get("provider") != "JRA official annual results":
        raise ValueError("manifest provider must identify JRA official annual results")
    if manifest.get("complete_races") is not True:
        raise ValueError("manifest must attest complete_races=true")
    if manifest.get("point_in_time_odds_included") is not False:
        raise ValueError("official result manifest must declare no point-in-time odds")
    if manifest.get("terms_url") != JRA_TERMS_URL:
        raise ValueError("manifest must preserve the JRA terms reference")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("manifest files must be non-empty")
    allowed = frozenset(int(year) for year in allowed_years)
    records: list[dict[str, Any]] = []
    seen_sources: set[str] = set()
    seen_runners: set[tuple[str, str]] = set()
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "format"}:
            raise ValueError("each manifest file needs exactly path, sha256 and format")
        if item["format"] != "jsonl":
            raise ValueError("official result imports accept JSONL only")
        data_path = _relative_file(manifest_path.parent, str(item["path"]))
        if sha256_file(data_path) != str(item["sha256"]).lower():
            raise ValueError(f"sha256 mismatch for {item['path']}")
        with data_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    record = _validate_record(json.loads(line), allowed)
                except (json.JSONDecodeError, ValueError, TypeError) as exc:
                    raise ValueError(f"{item['path']}:{line_number}: {exc}") from exc
                source_id = record["source_record_id"]
                runner_key = (record["race_id"], record["horse_id"])
                if source_id in seen_sources or runner_key in seen_runners:
                    raise ValueError("duplicate official result record in bundle")
                seen_sources.add(source_id)
                seen_runners.add(runner_key)
                records.append(record)
    if not records:
        raise ValueError("official result bundle contains no records")
    by_race: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_race.setdefault(record["race_id"], []).append(record)
    for race_id, runners in by_race.items():
        if len(runners) < 5:
            raise ValueError(f"race {race_id} contains fewer than five timed finishers")
        finishes = [int(row["payload"]["finish"]) for row in runners]
        numbers = [int(row["payload"]["horse_number"]) for row in runners]
        if finishes.count(1) != 1 or len(set(finishes)) != len(finishes):
            raise ValueError(f"race {race_id} has invalid finish positions")
        if len(set(numbers)) != len(numbers):
            raise ValueError(f"race {race_id} has duplicate horse numbers")
    dates = [record["race_date"] for record in records]
    return PreparedOfficialResultsImport(
        manifest=manifest,
        manifest_sha256=sha256_file(manifest_path),
        records=tuple(records),
        coverage_start=min(dates),
        coverage_end=max(dates),
    )


def _ensure_tables(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS official_history_imports (
            import_id TEXT PRIMARY KEY,
            manifest_sha256 TEXT NOT NULL UNIQUE,
            provider TEXT NOT NULL,
            terms_url TEXT NOT NULL,
            purpose TEXT NOT NULL,
            coverage_start TEXT NOT NULL,
            coverage_end TEXT NOT NULL,
            record_count INTEGER NOT NULL,
            imported_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS official_history_entries (
            source_record_id TEXT PRIMARY KEY,
            import_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            source_url TEXT NOT NULL,
            source_pdf_sha256 TEXT NOT NULL,
            race_id TEXT NOT NULL,
            horse_id TEXT NOT NULL,
            race_date TEXT NOT NULL,
            post_time TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            result_win_odds REAL,
            imported_at TEXT NOT NULL,
            FOREIGN KEY(import_id) REFERENCES official_history_imports(import_id),
            UNIQUE(provider, race_id, horse_id)
        );
        CREATE INDEX IF NOT EXISTS idx_official_history_race
            ON official_history_entries(race_id);
    """)


def apply_official_results_import(
    database: Path,
    prepared: PreparedOfficialResultsImport,
) -> dict[str, Any]:
    database = database.resolve()
    if not database.is_file():
        raise ValueError(f"database does not exist: {database}")
    import_id = f"jra-official-{prepared.manifest_sha256[:20]}"
    imported_at = datetime.now(timezone.utc).isoformat()
    inserted = 0
    skipped = 0
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        _ensure_tables(connection)
        connection.execute(
            """
            INSERT OR IGNORE INTO official_history_imports (
                import_id, manifest_sha256, provider, terms_url, purpose,
                coverage_start, coverage_end, record_count, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                import_id,
                prepared.manifest_sha256,
                prepared.manifest["provider"],
                prepared.manifest["terms_url"],
                prepared.manifest["purpose"],
                prepared.coverage_start,
                prepared.coverage_end,
                len(prepared.records),
                imported_at,
            ),
        )
        for record in prepared.records:
            existing = connection.execute(
                """
                SELECT source_record_id, payload_sha256, result_win_odds
                FROM official_history_entries
                WHERE source_record_id = ? OR (provider = ? AND race_id = ? AND horse_id = ?)
                """,
                (
                    record["source_record_id"],
                    prepared.manifest["provider"],
                    record["race_id"],
                    record["horse_id"],
                ),
            ).fetchone()
            if existing:
                if (
                    existing[0] != record["source_record_id"]
                    or existing[1] != record["payload_sha256"]
                    or existing[2] != record["result_win_odds"]
                ):
                    raise ValueError(
                        f"append-only conflict for {record['race_id']}/{record['horse_id']}"
                    )
                skipped += 1
                continue
            connection.execute(
                """
                INSERT INTO official_history_entries (
                    source_record_id, import_id, provider, source_url,
                    source_pdf_sha256, race_id, horse_id, race_date, post_time,
                    payload_json, payload_sha256, result_win_odds, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["source_record_id"],
                    import_id,
                    prepared.manifest["provider"],
                    record["source_url"],
                    record["source_pdf_sha256"],
                    record["race_id"],
                    record["horse_id"],
                    record["race_date"],
                    record["post_time"],
                    json.dumps(record["payload"], ensure_ascii=False, sort_keys=True),
                    record["payload_sha256"],
                    record["result_win_odds"],
                    imported_at,
                ),
            )
            inserted += 1
    return {
        "schema": "jra-official-results-import-report-v1",
        "database": str(database),
        "import_id": import_id,
        "manifest_sha256": prepared.manifest_sha256,
        "coverage_start": prepared.coverage_start,
        "coverage_end": prepared.coverage_end,
        "validated_record_count": len(prepared.records),
        "inserted_record_count": inserted,
        "idempotently_skipped_record_count": skipped,
        "append_only": True,
        "point_in_time_odds_imported": False,
    }
