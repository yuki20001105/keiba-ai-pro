"""Parser for finalized race pages served by db.sp.netkeiba.com."""
from __future__ import annotations

import asyncio
import gc
import re
from typing import Any, Awaitable, Callable

from bs4 import BeautifulSoup


JRA_VENUES = {
    "01": "\u672d\u5e4c",
    "02": "\u51fd\u9928",
    "03": "\u798f\u5cf6",
    "04": "\u65b0\u6f5f",
    "05": "\u6771\u4eac",
    "06": "\u4e2d\u5c71",
    "07": "\u4e2d\u4eac",
    "08": "\u4eac\u90fd",
    "09": "\u962a\u795e",
    "10": "\u5c0f\u5009",
}
BET_TYPES = {
    "\u5358\u52dd",
    "\u8907\u52dd",
    "\u67a0\u9023",
    "\u99ac\u9023",
    "\u30ef\u30a4\u30c9",
    "\u99ac\u5358",
    "3\u9023\u8907",
    "3\u9023\u5358",
}


def _normal(value: Any) -> str:
    return re.sub(r"[\s\u3000]+", "", str(value or ""))


def _number(value: Any, cast: Callable[[str], Any]) -> Any:
    text = str(value or "").strip().replace(",", "")
    try:
        return cast(text)
    except (TypeError, ValueError):
        return None


def _column(headers: list[str], name: str) -> int:
    normalized = _normal(name)
    for index, header in enumerate(headers):
        if normalized in _normal(header):
            return index
    return -1


def _parse_returns(soup: BeautifulSoup, race_id: str) -> list[dict[str, Any]]:
    tables = soup.find_all("table")
    if len(tables) < 2:
        return []
    results: list[dict[str, Any]] = []
    current_type = ""
    for row in tables[1].find_all("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
        if len(cells) < 3:
            continue
        if cells[0] in BET_TYPES:
            current_type = cells.pop(0)
        if not current_type or len(cells) < 3:
            continue
        payout = _number(re.sub(r"[^0-9,]", "", cells[1]), int)
        popularity = _number(re.sub(r"[^0-9]", "", cells[2]), int)
        if payout is None:
            continue
        results.append(
            {
                "race_id": race_id,
                "bet_type": current_type,
                "combinations": cells[0],
                "payout": payout,
                "popularity": popularity,
            }
        )
    return results


async def parse_mobile_race(
    session: Any,
    race_id: str,
    html: str,
    *,
    date_hint: str,
    quick_mode: bool,
    horse_detail_fetcher: Callable[..., Awaitable[dict[str, Any]]],
) -> dict[str, Any] | None:
    """Return the canonical race payload or ``None`` for an incompatible page."""
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", class_=lambda c: c and "ResultsByRaceDetail" in c)
    if table is None:
        return None

    header_value = soup.find("div", class_="RaceHeader_Value")
    header_text = header_value.get_text(" ", strip=True) if header_value else ""
    race_name_tag = soup.find("h2")
    race_name = race_name_tag.get_text(" ", strip=True) if race_name_tag else ""
    race_data_tag = soup.find("div", class_="RaceData")
    race_data_text = race_data_tag.get_text(" ", strip=True) if race_data_tag else header_text

    course_match = re.search(
        r"(?P<post>\d{1,2}:\d{2})\s*\u767a\u8d70\s*"
        r"(?P<track>\u829d|\u30c0(?:\u30fc\u30c8)?|\u969c\u5bb3)"
        r"(?P<distance>\d{3,4})m(?:\((?P<direction>[^)]+)\))?"
        r"(?:\s+(?P<weather>\S+))?(?:\s+(?P<condition>\S+))?",
        race_data_text,
    )
    if course_match is None:
        return None

    rows = table.find_all("tr")
    if len(rows) < 2:
        return None
    headers = [cell.get_text(" ", strip=True) for cell in rows[0].find_all(["th", "td"])]
    indices = {
        "finish": _column(headers, "\u7740\u9806"),
        "bracket": _column(headers, "\u67a0\u756a"),
        "number": _column(headers, "\u99ac\u756a"),
        "horse": _column(headers, "\u99ac\u540d"),
        "sex_age": _column(headers, "\u6027\u9f62"),
        "jockey_weight": _column(headers, "\u65a4\u91cf"),
        "jockey": _column(headers, "\u9a0e\u624b"),
        "time": _column(headers, "\u30bf\u30a4\u30e0"),
        "margin": _column(headers, "\u7740\u5dee"),
        "corner": _column(headers, "\u901a\u904e"),
        "last_3f": _column(headers, "\u4e0a\u304c\u308a"),
        "odds": _column(headers, "\u5358\u52dd"),
        "popularity": _column(headers, "\u4eba\u6c17"),
        "weight": _column(headers, "\u99ac\u4f53\u91cd"),
        "trainer": _column(headers, "\u8abf\u6559\u5e2b"),
        "owner": _column(headers, "\u99ac\u4e3b"),
        "prize": _column(headers, "\u8cde\u91d1"),
    }
    if any(indices[key] < 0 for key in ("finish", "number", "horse", "time", "odds")):
        return None

    horses: list[dict[str, Any]] = []
    for row in rows[1:]:
        cells = row.find_all("td")
        if not cells:
            continue

        def text(field: str) -> str:
            index = indices[field]
            return cells[index].get_text(" ", strip=True) if 0 <= index < len(cells) else ""

        def link(field: str) -> tuple[str, str]:
            index = indices[field]
            if not (0 <= index < len(cells)):
                return "", ""
            anchor = cells[index].find("a", href=True)
            if anchor is None:
                return text(field), ""
            return anchor.get_text(" ", strip=True) or text(field), str(anchor["href"])

        horse_name, horse_url = link("horse")
        horse_match = re.search(r"/horse/(?:result/)?([A-Za-z0-9]+)", horse_url)
        jockey_name, jockey_url = link("jockey")
        jockey_match = re.search(r"/jockey/([A-Za-z0-9]+)", jockey_url)
        trainer_name, trainer_url = link("trainer")
        trainer_match = re.search(r"/trainer/([A-Za-z0-9]+)", trainer_url)
        sex_age = text("sex_age")
        age_match = re.search(r"\d+", sex_age)
        weight_text = text("weight")
        weight_match = re.match(r"(\d+)\(([+-]?\d+)\)", weight_text)
        corner_text = text("corner")
        corners = [int(value) for value in corner_text.split("-") if value.strip().isdigit()]
        finish_text = text("finish")
        finish_position: int | str = int(finish_text) if finish_text.isdigit() else finish_text
        prize = _number(re.sub(r"[^0-9,.]", "", text("prize")), float)
        horses.append(
            {
                "race_id": race_id,
                "finish_position": finish_position,
                "bracket_number": _number(text("bracket"), int),
                "horse_number": _number(text("number"), int),
                "horse_name": horse_name,
                "horse_url": horse_url,
                "horse_id": horse_match.group(1) if horse_match else "",
                "sex_age": sex_age,
                "sex": sex_age[:1],
                "age": int(age_match.group()) if age_match else None,
                "jockey_weight": _number(text("jockey_weight"), float),
                "jockey_name": jockey_name,
                "jockey_url": jockey_url,
                "jockey_id": jockey_match.group(1) if jockey_match else "",
                "finish_time": text("time"),
                "margin": text("margin"),
                "odds": _number(text("odds"), float),
                "popularity": _number(text("popularity"), int),
                "corner_positions": corner_text,
                "corner_positions_list": corners,
                "corner_1": corners[0] if len(corners) > 0 else None,
                "corner_2": corners[1] if len(corners) > 1 else None,
                "corner_3": corners[2] if len(corners) > 2 else None,
                "corner_4": corners[3] if len(corners) > 3 else None,
                "last_3f": text("last_3f"),
                "weight": weight_text,
                "weight_kg": int(weight_match.group(1)) if weight_match else None,
                "weight_change": int(weight_match.group(2)) if weight_match else None,
                "trainer_name": trainer_name,
                "trainer_url": trainer_url,
                "trainer_id": trainer_match.group(1) if trainer_match else "",
                "owner_name": link("owner")[0],
                "prize_money": prize * 10000 if prize is not None else None,
            }
        )

    last_values = [
        _number(horse.get("last_3f"), float) or float("inf") for horse in horses
    ]
    for rank, index in enumerate(sorted(range(len(horses)), key=last_values.__getitem__), start=1):
        horses[index]["last_3f_rank"] = rank if last_values[index] != float("inf") else None

    unique_horses = {horse["horse_id"]: horse for horse in horses if horse.get("horse_id")}
    values = list(unique_horses.values())
    for start in range(0, len(values), 4):
        chunk = values[start : start + 4]
        details = await asyncio.gather(
            *[
                horse_detail_fetcher(
                    session,
                    horse["horse_id"],
                    horse["horse_url"],
                    pedigree_cache={},
                    quick_mode=quick_mode,
                )
                for horse in chunk
            ]
        )
        for horse, detail in zip(chunk, details):
            horse.update(detail)
        if start + 4 < len(values):
            await asyncio.sleep(1.0)
        gc.collect()

    class_match = re.search(r"\b(G[1-3]|L|OP)\b", header_text)
    if class_match:
        race_class = class_match.group(1)
    else:
        class_text = next(
            (
                value
                for value in (
                    "\u65b0\u99ac",
                    "\u672a\u52dd\u5229",
                    "1\u52dd\u30af\u30e9\u30b9",
                    "2\u52dd\u30af\u30e9\u30b9",
                    "3\u52dd\u30af\u30e9\u30b9",
                    "\u30aa\u30fc\u30d7\u30f3",
                )
                if value in header_text
            ),
            "\u4e0d\u660e",
        )
        race_class = class_text
    meeting = re.search(r"(\d+)\u56de[^\s]+?(\d+)\u65e5\u76ee", header_text)

    return {
        "race_info": {
            "race_id": race_id,
            "race_name": race_name,
            "venue": JRA_VENUES.get(race_id[4:6], race_id[4:6]),
            "date": date_hint,
            "post_time": course_match.group("post"),
            "race_class": race_class,
            "kai": int(meeting.group(1)) if meeting else None,
            "day": int(meeting.group(2)) if meeting else None,
            "course_direction": course_match.group("direction") or "",
            "distance": int(course_match.group("distance")),
            "track_type": (
                "\u30c0\u30fc\u30c8"
                if course_match.group("track") == "\u30c0"
                else course_match.group("track")
            ),
            "weather": course_match.group("weather") or "",
            "field_condition": course_match.group("condition") or "",
            "num_horses": len(horses),
            "surface": (
                "\u30c0\u30fc\u30c8"
                if course_match.group("track") == "\u30c0"
                else course_match.group("track")
            ),
            "lap_cumulative": {},
            "lap_sectional": {},
            "source_host": "db.sp.netkeiba.com",
        },
        "horses": horses,
        "return_tables": _parse_returns(soup, race_id),
    }
