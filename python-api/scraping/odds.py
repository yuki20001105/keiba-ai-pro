"""Point-in-time win-odds retrieval for netkeiba race pages."""
from __future__ import annotations

from typing import Any

try:
    from app_config import logger  # type: ignore
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


_ACTUAL_ODDS_STATUSES = {"middle", "result"}


def parse_tansho_odds_payload(
    payload: Any,
    *,
    allow_predicted: bool = False,
) -> tuple[dict[int, float], dict[int, int], str]:
    """Parse netkeiba's own page-update payload.

    ``status=yoso`` contains forecast odds rather than wager-time market odds,
    so it is rejected by default to preserve point-in-time evidence integrity.
    """

    if not isinstance(payload, dict):
        return {}, {}, "invalid"
    status = str(payload.get("status") or "").strip().lower()
    if status not in _ACTUAL_ODDS_STATUSES and not (allow_predicted and status == "yoso"):
        return {}, {}, status or "unknown"

    data = payload.get("data")
    odds_root = data.get("odds") if isinstance(data, dict) else None
    tansho = odds_root.get("1") if isinstance(odds_root, dict) else None
    if not isinstance(tansho, dict):
        return {}, {}, status

    odds: dict[int, float] = {}
    popularity: dict[int, int] = {}
    for raw_number, raw_values in tansho.items():
        try:
            horse_number = int(raw_number)
        except (TypeError, ValueError):
            continue
        values = raw_values if isinstance(raw_values, (list, tuple)) else [raw_values]
        try:
            price = float(values[0])
        except (IndexError, TypeError, ValueError):
            continue
        if price <= 0:
            continue
        odds[horse_number] = price
        try:
            rank = int(values[2])
        except (IndexError, TypeError, ValueError):
            rank = 0
        if rank > 0:
            popularity[horse_number] = rank
    return odds, popularity, status


async def fetch_tansho_odds_api(
    session: Any,
    race_id: str,
    *,
    allow_predicted: bool = False,
) -> tuple[dict[int, float], dict[int, int], str]:
    """Fetch the JSON endpoint used by netkeiba's own shutuba JavaScript."""

    url = "https://race.netkeiba.com/api/api_get_jra_odds.html"
    params = {"race_id": race_id, "type": "1", "action": "update"}
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}",
        "X-Requested-With": "XMLHttpRequest",
    }
    try:
        async with session.get(url, params=params, headers=headers) as response:
            if response.status != 200:
                logger.warning(f"[odds_api] HTTP {response.status}: {race_id}")
                return {}, {}, f"http-{response.status}"
            payload = await response.json(content_type=None)
    except Exception as exc:
        logger.warning(f"[odds_api] 取得失敗 {race_id}: {exc}")
        return {}, {}, "error"

    odds, popularity, status = parse_tansho_odds_payload(
        payload,
        allow_predicted=allow_predicted,
    )
    if status == "yoso" and not allow_predicted:
        logger.info(f"[odds_api] {race_id}: 予想オッズのため証跡入力には不採用")
    elif odds:
        logger.info(f"[odds_api] {race_id}: status={status} {len(odds)}頭取得")
    return odds, popularity, status
