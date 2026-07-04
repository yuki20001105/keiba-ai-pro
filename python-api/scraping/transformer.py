from __future__ import annotations


def normalize_race_ids(race_ids: list[str]) -> list[str]:
    # Keep input order while deduplicating.
    return list(dict.fromkeys([str(r).strip() for r in race_ids if str(r).strip()]))
