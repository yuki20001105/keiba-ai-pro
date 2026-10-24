"""JRA-only list resolution with verified month-index reuse.

The existing parser and provider-wide HTTP admission policy are unchanged.
Historical central-only jobs consult the fully paginated month index first,
instead of downloading an all-venue desktop list for every racing day.
"""
from __future__ import annotations

from datetime import date

import aiohttp

from .constants import get_random_headers
from .fetch_pipeline import FetchAccessBlocked
from . import race_list


def _is_jra_id(value: object, target: date) -> bool:
    return isinstance(value, str) and race_list._valid_jra_race_id(value, target)


async def fetch_jra_race_ids(
    date_str: str, *, force_refresh: bool = False,
) -> tuple[list[str], str]:
    """Return only validated central IDs; an access restriction never falls back.

    An old month index is reused only by the existing source/hash/TTL gate.
    An empty *fully verified* historical month entry is distinguished from an
    unverified or exclusively-local desktop response. Recent cards retain the
    existing current-card path so a partial results list cannot freeze a card.
    """
    target = date(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8]))
    if target.strftime("%Y%m%d") != date_str:
        raise ValueError("invalid JRA race-list date")

    attempts: list[dict] = []
    if not race_list._recent_or_future(target):
        timeout = aiohttp.ClientTimeout(total=25, connect=8)
        connector = aiohttp.TCPConnector(limit=2, limit_per_host=1)
        try:
            async with aiohttp.ClientSession(
                headers=get_random_headers(), timeout=timeout, connector=connector,
            ) as session:
                ids = await race_list._fetch_mobile_month_race_ids(
                    session, date_str, target, force_refresh=force_refresh,
                )
        except FetchAccessBlocked:
            raise
        except (aiohttp.ClientError, TimeoutError, RuntimeError) as exc:
            attempts.append({"url": race_list.build_mobile_month_url(date_str),
                             "reason": "historical_month_unconfirmed",
                             "error_type": type(exc).__name__})
        else:
            # Revalidate cached payloads as well as newly parsed entries. A
            # malformed/mixed cache must not become a verified-empty JRA day.
            if any(not _is_jra_id(value, target) for value in ids):
                raise race_list.RaceListUnavailable(
                    date_str, [{"reason": "historical_month_scope_mismatch"}],
                    reason="jra_scope_unconfirmed",
                )
            ids = list(dict.fromkeys(ids))
            source = "db.sp.netkeiba.com/month-search"
            return ids, source if ids else source + ":verified-empty"

    # A recoverable month-parser failure can use the ordinary exact-date
    # resolver. It does not gain permission to use local races or bad IDs.
    ids, source = await race_list.fetch_race_ids(date_str, force_refresh=force_refresh)
    central = list(dict.fromkeys(value for value in ids if _is_jra_id(value, target)))
    if central:
        return central, source
    if not ids and str(source) == "db.sp.netkeiba.com/month-search:verified-empty":
        return [], source
    attempts.append({"source": str(source), "reason": "no_verified_jra_ids"})
    raise race_list.RaceListUnavailable(
        date_str, attempts, reason="jra_scope_unconfirmed",
    )
