"""Pure, fail-closed merges for ordinary acquisition (never an HTTP client).

Only an unambiguously identical runner may inherit a known stored value. A
refresh may correct valid values, but a partial parse must not erase them.
Operational evidence stays outside the provider/training payload.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import math
import re


PRESERVATION_POLICY_VERSION = "normal-acquisition-safe-v1"
_MISSING = {"", "-", "--", "---", "—", "―", "－", "**", "***", "?", "n/a", "na", "null", "none", "unknown", "未取得", "不明", "nan", "inf", "+inf", "-inf"}
_NONFINISH = {"中止", "取消", "除外", "失格", "競走中止", "出走取消", "競走除外", "中", "取", "除", "失", "dnf", "dns", "dsq"}
_POSITIVE = {
    "horse_number", "bracket_number", "age", "distance", "num_horses", "race_number",
    "odds", "popularity", "weight_kg", "horse_weight", "jockey_weight", "burden_weight",
    "last_3f", "last_3f_time", "last_3f_rank", "corner_1", "corner_2", "corner_3", "corner_4",
}
_INTEGER = {"horse_number", "bracket_number", "age", "num_horses", "race_number", "popularity", "last_3f_rank"}
_FINITE = {"weight_change", "horse_weight_change"}
_CLOCKS = {"finish_time", "time", "time_seconds"}
_FINISH = {"finish_position", "finish"}
_RESULT_FIELDS = _CLOCKS | _FINISH | {
    "finish_position_raw", "finish_annotation", "last_3f", "last_3f_time", "last_3f_rank",
    "margin", "corner_positions", "corner_positions_list", "corner_1", "corner_2", "corner_3", "corner_4",
    "prize_money", "speed_deviation", "speed_figure", "running_style",
}


class PreservationConflict(ValueError):
    """The entire save must roll back, rather than guessing at identity."""


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _clock(value):
    match = re.fullmatch(r"(\d+):([0-5]?\d(?:\.\d+)?)", str(value).strip())
    return int(match[1]) * 60 + float(match[2]) if match else _number(value)


def _date(value):
    text = str(value).strip()
    if not re.fullmatch(r"\d{8}|\d{4}[-/]\d{2}[-/]\d{2}", text):
        return None
    try:
        return datetime.strptime(re.sub(r"\D", "", text), "%Y%m%d").date()
    except ValueError:
        return None


def _blank(value):
    return (
        value is None
        or isinstance(value, str) and value.strip().lower() in _MISSING
        or isinstance(value, (float, int)) and not isinstance(value, bool) and not math.isfinite(value)
        or isinstance(value, (list, dict)) and not value
    )


def known(field, value):
    """Zero is valid by default (weight change, prize, encoded categories)."""
    if _blank(value):
        return False
    if field in {"date", "race_date", "kaisai_date"}:
        return _date(value) is not None
    if field in _FINISH:
        if str(value).strip().lower() in _NONFINISH or re.fullmatch(r"[1-9]\d*\(降\)", str(value).strip()):
            return True
        number = _number(value)
        return number is not None and number > 0 and number.is_integer()
    if field in _CLOCKS:
        number = _clock(value)
        return number is not None and number > 0
    if field == "weight":
        match = re.fullmatch(r"(\d+)(?:\([+-]?\d+\))?", str(value).strip())
        return bool(match and int(match[1]) > 0)
    if field in _POSITIVE:
        number = _number(value)
        return number is not None and number > 0 and (field not in _INTEGER or number.is_integer())
    if field in _FINITE or field == "prize_money":
        number = _number(value)
        return number is not None and (field != "prize_money" or number >= 0)
    return True


def _merge_fields(old, new):
    result = deepcopy(old)
    result.pop("_acquisition_reuse", None)
    for field, value in new.items():
        if field == "_acquisition_reuse":
            continue
        if known(field, value) or not known(field, old.get(field)):
            # Non-finite values are not valid JSON and must never reach SQLite.
            result[field] = None if isinstance(value, (float, int)) and not isinstance(value, bool) and not math.isfinite(value) else deepcopy(value)
    return result


def _identity(horse, race_id):
    if not isinstance(horse, dict):
        raise PreservationConflict("runner-not-object")
    if not _blank(horse.get("race_id")) and str(horse["race_id"]) != race_id:
        raise PreservationConflict("runner-race-id-conflict")
    horse_id = None if _blank(horse.get("horse_id")) else str(horse["horse_id"]).strip()
    number = _number(horse.get("horse_number"))
    if not _blank(horse.get("horse_number")) and (number is None or number <= 0 or not number.is_integer()):
        raise PreservationConflict("runner-number-invalid")
    number = int(number) if number is not None else None
    if horse_id is None and number is None:
        raise PreservationConflict("runner-identity-missing")
    return horse_id, number


def _index(horses, race_id):
    ids, numbers = {}, {}
    for position, horse in enumerate(horses):
        horse_id, number = _identity(horse, race_id)
        for key, index in ((horse_id, ids), (number, numbers)):
            if key is not None:
                if key in index:
                    raise PreservationConflict("runner-identity-duplicate")
                index[key] = position
    return ids, numbers


def _sync_aliases(result, incoming, fields, converter):
    supplied = [(field, incoming[field]) for field in fields if known(field, incoming.get(field))]
    if not supplied:
        return
    values = [converter(value) for _, value in supplied]
    if any(value != values[0] for value in values[1:]):
        raise PreservationConflict("runner-alias-conflict:" + fields[0])
    for field in fields:
        if field in result and not known(field, incoming.get(field)):
            if converter(result[field]) != values[0]:
                result[field] = values[0] if field == "time_seconds" else deepcopy(supplied[0][1])


def merge_acquisition_payload(existing_info, existing_horses, incoming, *, overwrite=True):
    if not isinstance(incoming, dict) or not isinstance(incoming.get("race_info"), dict):
        raise PreservationConflict("race-not-object")
    info = incoming["race_info"]
    race_id = str(info.get("race_id") or "")
    if not re.fullmatch(r"\d{12}", race_id):
        raise PreservationConflict("race-id-invalid")
    horses = incoming.get("horses")
    if not isinstance(horses, list) or not horses:
        raise PreservationConflict("runners-missing")
    if not isinstance(existing_info, dict) or not isinstance(existing_horses, list):
        raise PreservationConflict("stored-payload-invalid")
    if not _blank(existing_info.get("race_id")) and str(existing_info["race_id"]) != race_id:
        raise PreservationConflict("stored-race-id-conflict")
    dates = {_date(item[field]) for item in (existing_info, info) for field in ("date", "race_date", "kaisai_date") if known(field, item.get(field))}
    if len(dates) > 1:
        raise PreservationConflict("race-date-conflict")
    old_ids, old_numbers = _index(existing_horses, race_id)
    _index(horses, race_id)
    used, merged, matched_positions = set(), [], []
    for horse in horses:
        horse_id, number = _identity(horse, race_id)
        matches = {index for index in (old_ids.get(horse_id), old_numbers.get(number)) if index is not None}
        if len(matches) > 1:
            raise PreservationConflict("runner-identity-crossed")
        old = {}
        matched_position = None
        if matches:
            index = matches.pop()
            if index in used or not overwrite:
                raise PreservationConflict("runner-already-matched")
            used.add(index)
            matched_position = index
            old = existing_horses[index]
            old_id, old_number = _identity(old, race_id)
            if horse_id is not None and old_id is not None and horse_id != old_id or number is not None and old_number is not None and number != old_number:
                raise PreservationConflict("runner-identity-conflict")
        # A newly explicit non-finisher must never inherit stale race results.
        nonfinish = any(str(horse.get(field, "")).strip().lower() in _NONFINISH for field in _FINISH)
        if nonfinish:
            old = {field: value for field, value in old.items() if field not in _RESULT_FIELDS}
        row = _merge_fields(old, horse)
        _sync_aliases(row, horse, ("last_3f", "last_3f_time"), _number)
        _sync_aliases(row, horse, ("finish_time", "time_seconds", "time"), _clock)
        _sync_aliases(row, horse, ("weight_kg", "horse_weight"), _number)
        _sync_aliases(row, horse, ("finish_position", "finish"), lambda value: _number(value) if _number(value) is not None else str(value).strip().lower())
        if "_acquisition_reuse" in horse:
            row["_acquisition_reuse"] = deepcopy(horse["_acquisition_reuse"])
        merged.append(row)
        matched_positions.append(matched_position)
    if overwrite and len(used) != len(existing_horses):
        raise PreservationConflict("stored-runner-omitted")
    result = deepcopy(incoming)
    result["race_info"] = _merge_fields(existing_info, info)
    result["horses"] = merged
    # Row identity is side-channel save metadata, never a training column.
    return result, matched_positions


def merge_payouts(existing, incoming, race_id):
    """A return-table row is a winning return, never a zero-valued losing bet.

    Missing rows/amounts keep a known return. A new unknown/nonpositive return
    fails closed; it cannot fabricate a zero or erase previously settled money.
    """
    if not isinstance(incoming, list):
        raise PreservationConflict("payouts-not-list")
    merged = {}
    for rows, is_new in ((existing, False), (incoming, True)):
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or not _blank(row.get("race_id")) and str(row["race_id"]) != race_id:
                raise PreservationConflict("payout-race-id-conflict")
            key = (str(row.get("bet_type") or "").strip(), str(row.get("combinations") or "").strip())
            if not all(key) or key in seen:
                raise PreservationConflict("payout-identity-invalid-or-duplicate")
            seen.add(key)
            amount = _number(row.get("payout"))
            if amount is None or amount <= 0 or not amount.is_integer():
                if is_new and key in merged:
                    continue
                raise PreservationConflict("payout-amount-invalid")
            popularity = row.get("popularity")
            if not known("popularity", popularity):
                popularity = merged.get(key, {}).get("popularity")
            merged[key] = {"bet_type": key[0], "combinations": key[1], "payout": int(amount), "popularity": popularity}
    return list(merged.values())
