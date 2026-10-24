"""Lossless acquisition-only normalization; never a historical model rewrite.

Known going abbreviations have explicit aliases. An annotated demotion rank
is numeric only when the same race/runner/raw label is found in the actual
recognized HTML result table's 着順 column. Unknown labels remain quarantined.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import math
import re
import unicodedata
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

VERSION = "acquisition-source-normalization-v1"
GOING_ALIASES = {"良": "良", "稍": "稍重", "稍重": "稍重", "重": "重", "不": "不良", "不良": "不良"}
NON_FINISHERS = frozenset({"取", "除", "中", "失", "取消", "除外", "中止", "失格", "出走取消", "競走除外", "競走中止"})


def _text(value):
    return unicodedata.normalize("NFKC", str(value)).strip() if value is not None else ""


def _column(value):
    return re.sub(r"\s+", "", _text(value))


def normalize_field_condition(value):
    text = _text(value)
    normalized = GOING_ALIASES.get(text)
    return {"value": normalized if normalized is not None else value, "raw": value,
            "status": "recognized" if normalized is not None else "missing" if not text else "unrecognized",
            "changed": normalized is not None and value != normalized,
            "reason": None if normalized is not None or not text else "unknown_going_token"}


def _race_bound_url(url, race_id):
    try:
        parts = urlsplit(str(url))
        if parts.scheme != "https" or parts.hostname not in {"db.netkeiba.com", "db.sp.netkeiba.com", "race.netkeiba.com"}:
            return False
        if parts.username or parts.password or parts.port not in (None, 443):
            return False
        path_ids = re.findall(r"/race/(\d{12})(?:/|$)", parts.path)
        query_ids = parse_qs(parts.query).get("race_id", [])
        stated = path_ids + query_ids
        return bool(stated) and all(value == race_id for value in stated)
    except (ValueError, TypeError):
        return False


def finish_column_evidence_from_html(html, *, race_id, source_url):
    """Bind displayed result ranks to this HTML's race and runner identity.

    The decoded hash identifies parser input, not historical availability.
    No source URL is fetched and no supplied date hint becomes source evidence.
    """
    if not isinstance(html, str) or not re.fullmatch(r"\d{12}", str(race_id)) or not _race_bound_url(source_url, race_id):
        return None
    soup = BeautifulSoup(html, "lxml")
    table = soup.select_one("table.race_table_01, table.RaceTable01, table.ResultsByRaceDetail")
    if table is None:
        return None
    rows = table.find_all("tr")
    if not rows:
        return None
    headers = [_column(cell.get_text(" ", strip=True)) for cell in rows[0].find_all(["th", "td"])]
    if headers.count("着順") != 1 or headers.count("馬番") != 1:
        return None
    finish_index, number_index = headers.index("着順"), headers.index("馬番")
    labels = {}
    for row in rows[1:]:
        cells = row.find_all("td")
        if len(cells) <= max(finish_index, number_index):
            continue
        number = _text(cells[number_index].get_text(strip=True))
        if not re.fullmatch(r"\d{1,2}", number) or not 1 <= int(number) <= 99:
            return None
        key = str(int(number))
        if key in labels:
            return None
        labels[key] = cells[finish_index].get_text(strip=True)
    if not labels:
        return None
    return {"schema": "netkeiba-result-finish-column-v1", "race_id": str(race_id),
            "source_url": source_url, "decoded_html_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
            "column_header": "着順", "horse_number_header": "馬番", "finish_labels_by_horse_number": labels,
            "meaning": "displayed result-table placing; not an inferred arrival order or source capture time"}


def _demotion_evidence_matches(value, evidence, race_id, horse_number):
    if not isinstance(evidence, dict) or evidence.get("schema") != "netkeiba-result-finish-column-v1":
        return False
    if (evidence.get("race_id") != str(race_id) or evidence.get("column_header") != "着順"
            or evidence.get("horse_number_header") != "馬番"
            or not _race_bound_url(evidence.get("source_url"), str(race_id))
            or not re.fullmatch(r"[0-9a-f]{64}", str(evidence.get("decoded_html_sha256") or ""))):
        return False
    number = _text(horse_number)
    if not re.fullmatch(r"\d{1,2}", number):
        return False
    labels = evidence.get("finish_labels_by_horse_number")
    if not isinstance(labels, dict):
        return False
    recorded = labels.get(str(int(number)))
    return isinstance(recorded, str) and _column(recorded) == _column(value)


def normalize_finish_position(value, *, column_evidence=None, race_id=None, horse_number=None):
    result = {"value": value, "raw": value, "status": "unrecognized", "changed": False,
              "annotation": None, "reason": None, "version": VERSION}
    text = _column(value)
    if value is None or text == "":
        return {**result, "status": "missing"}
    if isinstance(value, bool):
        return {**result, "status": "quarantined", "reason": "invalid_finish_boolean"}
    numeric = None
    if isinstance(value, (int, float)) and math.isfinite(float(value)) and float(value).is_integer():
        numeric = int(value)
    elif re.fullmatch(r"\d{1,2}", text):
        numeric = int(text)
    if numeric is not None and 1 <= numeric <= 99:
        return {**result, "value": numeric, "status": "numeric", "changed": value != numeric}
    if text in NON_FINISHERS:
        return {**result, "status": "nonfinisher"}
    demotion = re.fullmatch(r"([1-9]\d?)\(降\)", text)
    if demotion:
        if not _demotion_evidence_matches(value, column_evidence, race_id, horse_number):
            return {**result, "status": "quarantined", "annotation": "降",
                    "reason": "demotion_result_column_evidence_unverified"}
        return {**result, "value": int(demotion.group(1)), "status": "source_verified_demotion",
                "changed": True, "annotation": "降",
                "evidence_decoded_html_sha256": column_evidence["decoded_html_sha256"]}
    return {**result, "status": "quarantined", "reason": "unknown_finish_token"}


def normalize_race_payload(payload, *, finish_column_evidence=None):
    """Return a deep, lossless acquisition payload; originals are never erased."""
    if payload is None:
        return None
    result = deepcopy(payload)
    info = result.get("race_info")
    if not isinstance(info, dict):
        raise ValueError("normalization-race-info-required")
    issues = []
    going = normalize_field_condition(info.get("field_condition"))
    if going["changed"]:
        info.setdefault("field_condition_raw", going["raw"])
        info["field_condition"] = going["value"]
        info.setdefault("_source_normalization", {})["field_condition"] = {**going, "version": VERSION}
    elif going["reason"]:
        issues.append({"field": "field_condition", "reason": going["reason"]})
    for horse in result.get("horses", []):
        if not isinstance(horse, dict):
            raise ValueError("normalization-runner-object-required")
        value = horse.get("finish_position")
        normalized = normalize_finish_position(value, column_evidence=finish_column_evidence,
            race_id=info.get("race_id"), horse_number=horse.get("horse_number"))
        if normalized["changed"]:
            horse.setdefault("finish_position_raw", normalized["raw"])
            horse["finish_position"] = normalized["value"]
            if normalized["annotation"]:
                horse["finish_annotation"] = normalized["annotation"]
            horse.setdefault("_source_normalization", {})["finish_position"] = normalized
        elif normalized["status"] == "quarantined":
            horse.setdefault("finish_position_raw", normalized["raw"])
            horse.setdefault("_source_normalization", {})["finish_position"] = normalized
            issues.append({"field": "finish_position", "horse_number": horse.get("horse_number"),
                           "reason": normalized["reason"]})
    if issues:
        result["normalization_issues"] = issues
    result["source_normalization_version"] = VERSION
    return result
