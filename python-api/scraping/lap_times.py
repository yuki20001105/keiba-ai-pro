"""Conservative, offline lap observations; never pace labels or model inputs."""
from __future__ import annotations

import hashlib
import math
import re

from bs4 import BeautifulSoup


_SEMANTICS = {"ラップ": "sectional", "ハロンタイム": "sectional",
              "ペース": "cumulative", "累計": "cumulative", "累積": "cumulative"}
_NUMBER = r"\d+(?:\.\d+)?"
_TIME = rf"(?:\d+:\d{{2}}(?:\.\d+)?|{_NUMBER})"
_SEQUENCE = re.compile(rf"{_TIME}(?:\s*-\s*{_TIME})*")
_SUMMARY = re.compile(rf"\s*[\(（]{_NUMBER}\s*-\s*{_NUMBER}[\)）]\s*$")


def _text(tag):
    return tag.get_text(" ", strip=True).replace("\u3000", " ")


def _seconds(value):
    if not re.fullmatch(_TIME, value):
        return None
    if ":" in value:
        minutes, seconds = value.split(":")
        if float(seconds) >= 60:
            return None
        value = float(minutes) * 60 + float(seconds)
    else:
        value = float(value)
    return value if math.isfinite(value) and value > 0 else None


def _row_times(cells, label):
    values = []
    for cell in cells:
        text = _text(cell)
        if label == "ペース":
            text = _SUMMARY.sub("", text).strip()
        if not _SEQUENCE.fullmatch(text):
            return None
        for token in re.split(r"\s*-\s*", text):
            value = _seconds(token)
            if value is None:
                return None
            values.append(value)
    return values or None


def _distance(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return int(number) if math.isfinite(number) and number.is_integer() and 0 < number <= 10000 else None


def _race_metadata(soup):
    distances = set()
    obstacle = False
    for tag in soup.select(".mainrace_data, .RaceData01, .data_intro dd"):
        text = _text(tag)
        obstacle |= "障" in text
        for match in re.finditer(r"(?:芝|ダート|ダ|障)[^\d]{0,12}(\d{2,5})\s*m", text):
            value = _distance(match.group(1))
            if value is not None:
                distances.add(value)
    return distances, obstacle


def _finish_time(soup):
    times = []
    for table in soup.select("table.race_table_01, table.RaceTable01"):
        rows = table.find_all("tr")
        if not rows:
            continue
        headers = [_text(cell).replace(" ", "") for cell in rows[0].find_all(["th", "td"])]
        columns = [i for i, text in enumerate(headers) if text in {"タイム", "走破タイム"}]
        if len(columns) != 1:
            continue
        for row in rows[1:]:
            cells = row.find_all(["th", "td"])
            if columns[0] < len(cells):
                text = _text(cells[columns[0]])
                # Fastest runner, not official position 1: demotions can change placings.
                value = _seconds(text) if ":" in text else None
                if value is not None:
                    times.append(value)
    return min(times) if times else None


def _parse_table(table, distance):
    rows = table.find_all("tr")
    headers = None
    observations = {}
    labels = {}
    for row in rows:
        cells = row.find_all(["th", "td"])
        if not cells:
            continue
        texts = [_text(cell).replace(" ", "") for cell in cells]
        label = texts[0]
        if label in _SEMANTICS:
            semantics = _SEMANTICS[label]
            if semantics in observations or any(str(cell.get(attr, "1")) != "1"
                                               for cell in cells for attr in ("colspan", "rowspan")):
                return None
            values = _row_times(cells[1:], label)
            if values is None:
                return None
            observations[semantics] = values
            labels[semantics] = label
        else:
            distance_cells = texts if re.fullmatch(r"\d+m?", label) else texts[1:]
            if distance_cells and all(re.fullmatch(r"\d+m?", text) for text in distance_cells):
                if headers is not None or any(str(cell.get(attr, "1")) != "1"
                                              for cell in cells for attr in ("colspan", "rowspan")):
                    return None
                headers = [int(text.removesuffix("m")) for text in distance_cells]

    if not observations:
        return None
    if distance is None and headers:
        distance = _distance(headers[-1])
    if distance is None:
        return None
    first_segment = distance % 200 if distance % 200 != 0 else 200
    keys = list(range(first_segment, distance + 1, 200))
    if headers is not None and headers != keys:
        return None
    if any(len(values) != len(keys) for values in observations.values()):
        return None

    sectional = observations.get("sectional")
    cumulative = observations.get("cumulative")
    if sectional is None:
        previous = 0.0
        sectional = []
        for value in cumulative:
            sectional.append(round(value - previous, 6))
            previous = value
    total = 0.0
    derived = []
    previous_distance = 0
    for key, value in zip(keys, sectional):
        segment_distance = key - previous_distance
        # Broad flat-racing bounds, also allowing a standing-start short segment.
        if not math.isfinite(value) or value < 0.5 or not 4 <= segment_distance / value <= 25:
            return None
        total += value
        derived.append(round(total, 6))
        previous_distance = key
    if cumulative is not None and any(abs(left - right) > 0.11
                                      for left, right in zip(cumulative, derived)):
        return None
    cumulative = cumulative if cumulative is not None else derived
    return {"lap_sectional": dict(zip(keys, sectional)),
            "lap_cumulative": dict(zip(keys, cumulative)),
            "lap_evidence": {
                "schema": "lap-observation-v1",
                "source_table_classes": list(table.get("class", [])),
                "source_table_sha256": hashlib.sha256(str(table).encode("utf-8")).hexdigest(),
                "sectional_source": labels.get("sectional", "derived_from_cumulative"),
                "cumulative_source": labels.get("cumulative", "derived_from_sectional"),
                "distance_key_semantics": "metres_from_start_at_segment_end",
                "total_distance": distance,
                "initial_segment_distance": first_segment,
                "distance_source": "distance_headers" if headers is not None else "race_metadata",
                "finish_time_check": "not_available",
                "is_verified_source_identity": False,
                "is_pace_label": False,
            }}


def parse_lap_times(soup: BeautifulSoup, *, race_distance=None) -> dict:
    """Return ``lap_sectional``, ``lap_cumulative`` and ``lap_evidence``.

    Maps use integer cumulative-distance keys and seconds. Missing, ambiguous or
    inconsistent observations return two empty maps and evidence=None. Accepts
    explicit ラップ/ペース rows (including the latter's parenthesized 3F summary),
    or labeled distance columns in a recognized lap table. A short initial
    segment precedes regular 200m segments; implicit obstacle splits are rejected.

    Evidence describes parsed semantics; its hash covers the BeautifulSoup table
    serialized as UTF-8, not response bytes. It does not verify publisher identity,
    event/capture dates or authorization. Callers must independently establish
    those before training on strictly prior races. This helper performs no I/O.
    """
    empty = {"lap_sectional": {}, "lap_cumulative": {}, "lap_evidence": None}
    supplied = _distance(race_distance) if race_distance is not None else None
    if race_distance is not None and supplied is None:
        return empty
    metadata, obstacle = _race_metadata(soup)
    if obstacle or len(metadata) > 1 or (supplied is not None and metadata and supplied not in metadata):
        return empty
    distance = supplied if supplied is not None else next(iter(metadata), None)
    parsed = []
    for table in soup.find_all("table"):
        caption = table.find("caption")
        description = str(table.get("summary", "")) + (_text(caption) if caption else "")
        if "ラップタイム" not in description and "Race_HaronTime" not in table.get("class", []):
            continue
        result = _parse_table(table, distance)
        if result is None:
            return empty
        parsed.append(result)
    if not parsed or any(result["lap_sectional"] != parsed[0]["lap_sectional"]
                         or result["lap_cumulative"] != parsed[0]["lap_cumulative"] for result in parsed[1:]):
        return empty
    result = parsed[0]
    finish = _finish_time(soup)
    if finish is not None:
        total = list(result["lap_cumulative"].values())[-1]
        if abs(total - finish) > 0.3 + 1e-9:
            return empty
        result["lap_evidence"]["finish_time_check"] = "matched"
        result["lap_evidence"]["finish_time_seconds"] = finish
    if supplied is not None and result["lap_evidence"]["distance_source"] == "race_metadata":
        result["lap_evidence"]["distance_source"] = "race_distance_argument"
    return result
