"""Strict netkeiba adapters validated against retained real 2026-09-19 HTML.

Only explicit current-card cells become inputs. Nested memo/history tables,
market columns and results never enter the feature map. Unknown layouts fail
closed. Provider clocks are explicitly interpreted as Japan Standard Time.
"""
from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
import unicodedata
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup
from experiments.dataset import JRA_VENUES, FLAT_DISTANCE_CONTRACT

JST = timezone(timedelta(hours=9))
SCHEMA = "netkeiba-live-normalized-v2"


def text(node):
    return unicodedata.normalize("NFKC", node.get_text(" ", strip=True) if node else "")


def soup_for(body):
    if not isinstance(body, bytes) or not 0 < len(body) <= 2 * 1024 * 1024:
        raise ValueError("raw-provider-body-required")
    return BeautifulSoup(body, "lxml")


def aware(value):
    t = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(t, datetime) or t.tzinfo is None or t.utcoffset() is None:
        raise ValueError("timezone-required")
    return t


def identity(soup, race_id, post_time, *, result=False):
    post = aware(post_time).astimezone(JST)
    if not re.fullmatch(r"\d{12}", race_id) or race_id[4:6] not in JRA_VENUES or race_id[:4] != post.strftime("%Y"):
        raise ValueError("race-identity-invalid")
    if post.second or post.microsecond:
        raise ValueError("provider-post-time-minute-required")
    dates = {f"{int(y):04d}{int(m):02d}{int(d):02d}" for y, m, d in re.findall(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text(soup.title))}
    if dates != {post.strftime("%Y%m%d")}:
        raise ValueError("provider-card-date-unproven")
    links = soup.select('link[rel="canonical"]')
    if len(links) != 1:
        raise ValueError("canonical-race-required")
    url = urlsplit(links[0].get("href", ""))
    expected_path = "/race/result.html" if result else "/race/shutuba.html"
    if (url.scheme != "https" or url.hostname != "race.netkeiba.com" or url.path != expected_path
            or url.username or url.port not in (None, 443) or parse_qs(url.query).get("race_id") != [race_id]):
        raise ValueError("canonical-race-mismatch")
    info = text(soup.select_one(".RaceData01"))
    tick = re.search(r"(\d{1,2}:\d{2})\s*発走", info)
    if not tick or tick[1].zfill(5) != post.strftime("%H:%M"):
        raise ValueError("provider-post-time-mismatch")
    return post


def parse_navigation(body, race_id, post_time):
    soup = soup_for(body)
    identity(soup, race_id, post_time)
    found = []
    for link in soup.select(".RaceNumWrap a"):
        values = parse_qs(urlsplit(link.get("href", "")).query).get("race_id", [])
        if len(values) != 1 or not re.fullmatch(r"\d{12}", values[0]) or values[0][:10] != race_id[:10]:
            raise ValueError("venue-navigation-identity-invalid")
        if text(link) != f"{int(values[0][-2:])}R":
            raise ValueError("venue-navigation-number-invalid")
        found.append(values[0])
    if len(found) != len(set(found)) or not found or sorted(int(x[-2:]) for x in found) != list(range(1, len(found) + 1)) or len(found) > 12:
        raise ValueError("venue-navigation-incomplete")
    return found


def _entity(cell, kind):
    anchors = cell.select(f'a[href*="/{kind}/"]') if cell else []
    if len(anchors) != 1:
        raise ValueError(f"current-{kind}-identity-required")
    path = urlsplit(anchors[0]["href"])
    match = re.fullmatch(rf"/{kind}/(?:result/recent/)?(\d+)/?", path.path)
    if path.scheme != "https" or path.hostname != "db.netkeiba.com" or not match:
        raise ValueError(f"current-{kind}-identity-invalid")
    return match[1]


def parse_card(body, race_id, post_time):
    soup = soup_for(body)
    post = identity(soup, race_id, post_time)
    info, meta = text(soup.select_one(".RaceData01")), text(soup.select_one(".RaceData02"))
    if re.search(r"障|ジャンプ|J[・.]?G", text(soup.title) + info + meta):
        raise ValueError("outside-flat-scope")
    course = re.search(r"(芝|ダ(?:ート)?)\s*(\d{3,4})m", info)
    count = re.findall(r"(?<!\d)(\d{1,2})頭", meta)
    if not course or len(count) != 1 or not 2 <= int(count[0]) <= 18 or JRA_VENUES[race_id[4:6]] not in meta:
        raise ValueError("current-race-metadata-required")
    surface, distance = ("turf" if course[1] == "芝" else "dirt"), int(course[2])
    if distance not in FLAT_DISTANCE_CONTRACT[surface]:
        raise ValueError("outside-flat-distance-contract")
    grade = None
    # Same compact labels as historical canonical rows, not the race's title.
    for item in soup.select(".RaceName [class*=Icon_GradeType]"):
        for cls in item.get("class", []):
            if cls in {"Icon_GradeType1", "Icon_GradeType2", "Icon_GradeType3"}:
                grade = {"1": "G1", "2": "G2", "3": "G3"}[cls[-1]]
    if grade is None:
        for token, value in (("3勝", "3勝クラス"), ("2勝", "2勝クラス"), ("1勝", "1勝クラス"),
                             ("未勝利", "未勝利"), ("新馬", "新馬"), ("オープン", "オープン")):
            if token in meta:
                grade = value
                break
    weather = re.search(r"天候:([^ /]+)", info)
    condition = re.search(r"馬場:([^ /]+)", info)
    tables = soup.select("table.Shutuba_Table.ShutubaTable")
    if len(tables) != 1 or soup.select_one("#All_Result_Table"):
        raise ValueError("current-shutuba-table-required")
    table = tables[0]
    records = []
    for row in table.find_all("tr"):
        # A modal embedded in the table contains nested historical rows.
        if row.find_parent("table") is not table:
            continue
        cells = row.find_all("td", recursive=False)
        if not cells:
            continue
        if len(cells) < 9 or "HorseList" not in row.get("class", []):
            raise ValueError("unknown-top-level-card-row")
        # Restrict status to actual card cells; free-text personal notes do not
        # become data or scratch evidence.
        if any(w in " ".join(text(c) for c in cells[:9]) for w in ("取消", "除外", "中止")):
            raise ValueError("scratched-card-requires-explicit-skip")
        if not text(cells[1]).isdecimal() or not text(cells[0]).isdecimal():
            raise ValueError("card-number-required")
        number, bracket = int(text(cells[1])), int(text(cells[0]))
        sexage = re.fullmatch(r"([牡牝セ])\s*(\d{1,2})", text(cells[4]))
        burden = text(cells[5])
        if not 1 <= number <= 18 or not 1 <= bracket <= 8 or not sexage or not re.fullmatch(r"\d{2}(?:\.\d)?", burden):
            raise ValueError("current-runner-basics-invalid")
        mass = text(cells[8]).replace(" ", "")
        horse_weight = change = None
        if mass not in ("", "--", "計不", "未計測"):
            weight = re.fullmatch(r"(\d{3})\(([+-]?\d+)\)", mass)
            if not weight or not 250 <= int(weight[1]) <= 650 or abs(int(weight[2])) > 100:
                raise ValueError("invalid-current-body-weight")
            horse_weight, change = int(weight[1]), int(weight[2])
        records.append({"race_id": race_id, "race_date": post.strftime("%Y%m%d"),
                        "horse_id": _entity(cells[3], "horse"), "horse_number": number,
                        "distance": distance, "surface": surface, "venue": JRA_VENUES[race_id[4:6]],
                        "num_horses": int(count[0]), "bracket_number": bracket,
                        "sex": sexage[1], "age": int(sexage[2]), "burden_weight": float(burden),
                        "horse_weight": horse_weight, "horse_weight_change": change,
                        "field_condition": condition[1] if condition else None,
                        "weather": weather[1] if weather else None, "race_class": grade,
                        "jockey_id": _entity(cells[6], "jockey"), "trainer_id": _entity(cells[7], "trainer"),
                        "sire": None, "damsire": None})
    if (len(records) != int(count[0]) or len({r["horse_number"] for r in records}) != len(records)
            or len({r["horse_id"] for r in records}) != len(records)):
        raise ValueError("complete-unique-card-required")
    return sorted(records, key=lambda x: x["horse_number"])


def parse_odds(body, race_id, *, received_at):
    data = json.loads(body)
    if data.get("status") != "middle":
        raise ValueError("pre-race-middle-odds-required")
    d = data["data"]
    expected = {"yy": race_id[2:4], "jyo": race_id[4:6], "kai": race_id[6:8], "nichi": race_id[8:10], "rno": race_id[10:12]}
    if any(str(d.get(k, "")).zfill(2) != v for k, v in expected.items()):
        raise ValueError("odds-race-identity-mismatch")
    raw_clock = d.get("official_datetime")
    # Real official_datetime is YYYY-MM-DD HH:MM:SS in provider JST; never
    # substitute receipt time for an absent/unparseable provider tick.
    tick = datetime.strptime(raw_clock, "%Y-%m-%d %H:%M:%S").replace(tzinfo=JST)
    if tick > aware(received_at):
        raise ValueError("provider-clock-in-future")
    prices = {}
    for key, values in d["odds"]["1"].items():
        if not re.fullmatch(r"\d{1,2}", key) or not isinstance(values, list) or not values:
            raise ValueError("invalid-odds-row")
        number, price = int(key), float(values[0])
        if number in prices or not 1 <= number <= 18 or not math.isfinite(price) or price < 1:
            raise ValueError("invalid-odds-row")
        prices[number] = price
    if not 2 <= len(prices) <= 18 or int(d.get("shusso", 0)) != len(prices) or int(d.get("touroku", 0)) != len(prices):
        raise ValueError("complete-odds-field-required")
    return {"odds": prices, "provider_time": tick.isoformat(), "provider_timezone": "Asia/Tokyo"}


def parse_calendar(body, year, month):
    soup = soup_for(body)
    selected = soup.select_one("#cal_select_year option[selected]")
    active = soup.select_one(".CalendarMonth .Active a")
    if (not selected or selected.get("value") != str(year) or not active
            or parse_qs(urlsplit(active.get("href", "")).query) != {"year": [str(year)], "month": [str(month)]}):
        raise ValueError("calendar-month-unproven")
    inverse = {v: k for k, v in JRA_VENUES.items()}
    days = {}
    for cell in soup.select("table.Calendar_Table td.RaceCellBox"):
        day = text(cell.select_one(".Day"))
        if not day:
            continue
        if not day.isdecimal() or int(day) in days:
            raise ValueError("calendar-day-ambiguous")
        venues = [text(v) for v in cell.select(".JyoName")]
        if len(venues) != len(set(venues)) or any(v not in inverse for v in venues):
            raise ValueError("calendar-venue-invalid")
        days[int(day)] = sorted(inverse[v] for v in venues)
    if set(days) != set(range(1, calendar.monthrange(year, month)[1] + 1)):
        raise ValueError("calendar-month-incomplete")
    return {f"{year:04d}{month:02d}{d:02d}": v for d, v in days.items()}


def parse_race_list(body, day, *, receipt):
    datetime.strptime(day, "%Y%m%d")
    for key in ("request_url", "url"):
        url = urlsplit(receipt.get(key, ""))
        if (url.scheme != "https" or url.hostname != "race.netkeiba.com" or url.path != "/top/race_list_sub.html"
                or url.username or url.port not in (None, 443) or parse_qs(url.query).get("kaisai_date") != [day]):
            raise ValueError("race-list-date-endpoint-mismatch")
    if receipt.get("body_sha256") != hashlib.sha256(body).hexdigest():
        raise ValueError("race-list-raw-receipt-mismatch")
    soup = soup_for(body)
    records, excluded, ids = [], [], []
    for item in soup.select(".RaceList_DataItem"):
        names = set()
        for link in item.select("a[href]"):
            names.update(parse_qs(urlsplit(link["href"]).query).get("race_id", []))
        if len(names) != 1:
            raise ValueError("list-card-identity-invalid")
        race_id = names.pop()
        if not re.fullmatch(r"\d{12}", race_id) or race_id[:4] != day[:4] or race_id[4:6] not in JRA_VENUES or race_id in ids:
            raise ValueError("list-card-identity-invalid")
        ids.append(race_id)
        description = text(item.select_one(".RaceList_ItemLong")) + text(item.select_one(".ItemTitle"))
        if re.search(r"障|ジャンプ", description):
            excluded.append({"race_id": race_id, "reason": "jump"})
            continue
        if not re.search(r"(?:芝|ダ(?:ート)?)\d{3,4}m", description):
            raise ValueError("unknown-list-race-scope")
        clock = text(item.select_one(".RaceList_Itemtime"))
        post = datetime.strptime(day + " " + clock, "%Y%m%d %H:%M").replace(tzinfo=JST)
        records.append({"race_id": race_id, "post_time": post.isoformat(), "race_type": "flat", "venue": race_id[4:6]})
    if not ids:
        raise ValueError("unpublished-or-empty-list-not-no-racing")
    # This date-bound fragment alone is NOT completeness evidence. Runtime
    # intersects these IDs with separately dated venue card navigation.
    return {"races": records, "expected_race_ids": ids, "excluded_races": excluded}


def parse_result(body, race_id, post_time, *, receipt):
    if receipt.get("body_sha256") != hashlib.sha256(body).hexdigest():
        raise ValueError("result-raw-receipt-mismatch")
    expected_url = f"https://race.netkeiba.com/race/result.html?race_id={race_id}"
    if receipt.get("url") != expected_url or receipt.get("request_url") != expected_url:
        raise ValueError("result-receipt-url-mismatch")
    soup = soup_for(body)
    post = identity(soup, race_id, post_time, result=True)
    if aware(receipt["received_at"]) <= post:
        raise ValueError("result-observed-before-post")
    table = soup.select_one("#All_Result_Table[summary='全着順']")
    meta = text(soup.select_one(".RaceData02"))
    counts = re.findall(r"(?<!\d)(\d{1,2})頭", meta)
    if table is None or len(counts) != 1:
        raise ValueError("complete-published-result-required")
    numbers, winners, ranks = [], [], {}
    for row in table.select("tr.HorseList"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 8 or not text(cells[2]).isdecimal():
            raise ValueError("result-runner-invalid")
        number, rank = int(text(cells[2])), text(cells[0])
        if not 1 <= number <= 18 or number in numbers or (not rank.isdecimal() and rank not in ("中止", "失格")):
            raise ValueError("result-status-needs-manual-review")
        numbers.append(number)
        if rank.isdecimal():
            if not 1 <= int(rank) <= int(counts[0]):
                raise ValueError("result-rank-out-of-range")
            ranks[number] = int(rank)
        if rank == "1":
            winners.append(number)
    payout_rows = soup.select(".Payout_Detail_Table tr.Tansho")
    if len(numbers) != int(counts[0]) or not winners or len(payout_rows) != 1:
        raise ValueError("result-or-win-payout-incomplete")
    nums = [int(text(s)) for s in payout_rows[0].select("td.Result span") if text(s)]
    amounts = [int(v.replace(",", "")) for v in re.findall(r"([\d,]+)円", text(payout_rows[0].select_one("td.Payout")))]
    if set(nums) != set(winners) or len(nums) != len(set(nums)) or len(nums) != len(amounts) or any(v < 100 or v % 10 for v in amounts):
        raise ValueError("confirmed-winner-payout-mismatch")
    return {"race_id": race_id, "status": "final", "complete": True, "runner_numbers": sorted(numbers),
            "winners": sorted(winners), "win_payouts": {str(n): v for n, v in zip(nums, amounts)},
            "refunded_numbers": [], "ranks": ranks, "source_url": receipt["url"],
            "source_sha256": receipt["body_sha256"], "observed_at": receipt["received_at"],
            "received_at": receipt["received_at"], "evidence": "published complete result and win-payout table; provider is netkeiba, not signed JRA evidence"}
