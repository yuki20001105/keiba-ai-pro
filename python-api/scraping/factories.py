from __future__ import annotations

from pathlib import Path
from typing import Callable

from scraping.parser import parse_db_list_race_ids, parse_race_list_sub_race_ids
from scraping.repository import ScrapingRepository
from scraping.transformer import normalize_race_ids
from scraping.validator import validate_race_ids


def parser_factory(kind: str) -> Callable[[str], list[str]]:
    if kind == "db_race_list":
        return parse_db_list_race_ids
    if kind == "race_list_sub":
        return parse_race_list_sub_race_ids
    raise ValueError(f"unsupported parser kind: {kind}")


def validator_factory(kind: str) -> Callable[[list[str]], object]:
    if kind == "race_ids":
        return validate_race_ids
    raise ValueError(f"unsupported validator kind: {kind}")


def transformer_factory(kind: str) -> Callable[[list[str]], list[str]]:
    if kind == "race_ids":
        return normalize_race_ids
    raise ValueError(f"unsupported transformer kind: {kind}")


def repository_factory(kind: str, *, db_path: Path):
    if kind == "scraping":
        return ScrapingRepository(db_path)
    raise ValueError(f"unsupported repository kind: {kind}")
