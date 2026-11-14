"""Source-confirmed research backfill for unobserved conditional aggregates."""
from __future__ import annotations

import math

from bs4 import BeautifulSoup
import pandas as pd

from experiments.dataset import _time
from experiments.features import _band
from scraping.last3f_evidence import _text, parse_last3f_evidence


def parse_history_evidence(html: str, **kwargs) -> dict:
    proof = parse_last3f_evidence(html, **kwargs)
    soup = BeautifulSoup(html, "lxml")
    tables = []
    for table in soup.select("table.race_table_01,table.RaceTable01,table.ResultsByRaceDetail"):
        rows = table.find_all("tr")
        if not rows:
            continue
        headers = [_text(cell.get_text(" ", strip=True)) for cell in rows[0].find_all(["th", "td"])]
        if all(headers.count(name) == 1 for name in ("馬番", "馬名", "着順", "タイム")):
            tables.append((rows, headers))
    if len(tables) != 1:
        raise ValueError("history-result-columns-not-unique")
    rows, headers = tables[0]
    number_index, finish_index, time_index = [headers.index(name) for name in ("馬番", "着順", "タイム")]
    values = {}
    for row in rows[1:]:
        cells = row.find_all("td")
        if len(cells) <= max(number_index, finish_index, time_index):
            continue
        number = int(_text(cells[number_index].get_text()))
        finish_text = _text(cells[finish_index].get_text())
        finish = int(finish_text) if finish_text.isdecimal() else None
        seconds = _time(cells[time_index].get_text(strip=True))
        values[number] = {
            "finish": finish if finish is not None and finish > 0 else None,
            "time_seconds": seconds if math.isfinite(seconds) and seconds > 0 else None,
        }
    for row in proof["rows"]:
        if row["horse_number"] not in values:
            raise ValueError("history-result-row-incomplete")
        row.update(values[row["horse_number"]])
    proof["schema"] = "single-race-history-source-v1"
    return proof


def backfill_conditionals(target: pd.DataFrame, observations: list[dict]) -> tuple[pd.DataFrame, list[dict]]:
    """Only extend empty conditional histories; leave existing features intact."""
    result, changes = target.copy(deep=True), []
    identities = [(row["race_id"], row["horse_id"]) for row in observations]
    if len(set(identities)) != len(identities):
        raise ValueError("history-observation-duplicate")
    for position, row in result.iterrows():
        for group in ("horse_distance_band", "horse_course"):
            count_column = f"safe_{group}_starts"
            columns = [f"safe_{group}_{field}" for field in ("win_rate", "top3_rate", "avg_speed_mps")]
            if row[count_column] != 0 or not row[columns].isna().all():
                continue
            candidates = []
            for observation in observations:
                if observation["horse_id"] != row["horse_id"]:
                    continue
                if observation["race_date"] >= row["race_date"]:
                    raise ValueError("history-observation-not-strictly-prior")
                if not (observation["finish"] >= 1 and observation["time_seconds"] > 0):
                    raise ValueError("history-observation-result-invalid")
                matches = (
                    _band(observation["distance"]) == _band(row["distance"])
                    if group == "horse_distance_band"
                    else observation["venue"] == row["venue"] and observation["surface"] == row["surface"]
                )
                if matches:
                    candidates.append(observation)
            if not candidates:
                continue
            count = len(candidates)
            values = [sum(item["finish"] == 1 for item in candidates) / count,
                      sum(item["finish"] <= 3 for item in candidates) / count,
                      sum(item["distance"] / item["time_seconds"] for item in candidates) / count]
            result.loc[position, count_column] = count
            for column, value in zip(columns, values):
                result.loc[position, column] = value
            changes.append({
                "horse_id": row["horse_id"], "horse_number": int(row["horse_number"]), "group": group,
                "source_races": sorted(item["race_id"] for item in candidates),
                "filled_columns": columns, "prior_count_before": 0, "prior_count_after": count,
                "selection_change": "Source-confirmed individual history, not a changed training target cohort.",
            })
    return result, changes
