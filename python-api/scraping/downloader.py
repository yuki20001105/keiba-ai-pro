from __future__ import annotations

import asyncio

import httpx

from scraping.cache import html_cache_path, read_html, write_html
from scraping.constants import SCRAPE_PROXY_URL
from scraping.models import DownloadResult
from scraping.settings import BACKOFF_BASE_SEC, MAX_RETRY


async def fetch_db_race_list_html(session, date: str, *, use_cache: bool = True) -> DownloadResult:
    url = f"https://db.netkeiba.com/race/list/{date}/"
    cache_path = html_cache_path("race_list", date)

    if use_cache:
        cached = read_html(cache_path)
        if cached:
            return DownloadResult(url=url, status_code=200, html=cached, cache_hit=True)

    kwargs = {}
    if SCRAPE_PROXY_URL:
        kwargs["proxy"] = SCRAPE_PROXY_URL

    for attempt in range(MAX_RETRY):
        try:
            async with session.get(url, **kwargs) as resp:
                body = await resp.read()
                html = body.decode("euc-jp", errors="ignore") if body else ""
                if resp.status == 200:
                    write_html(cache_path, html)
                    return DownloadResult(url=url, status_code=200, html=html, cache_hit=False)
                if resp.status == 429 and attempt < MAX_RETRY - 1:
                    await asyncio.sleep(BACKOFF_BASE_SEC * (attempt + 1))
                    continue
                return DownloadResult(url=url, status_code=resp.status, html=html, cache_hit=False)
        except Exception as exc:
            if attempt < MAX_RETRY - 1:
                await asyncio.sleep(BACKOFF_BASE_SEC * (attempt + 1))
                continue
            return DownloadResult(url=url, status_code=0, html=None, cache_hit=False, error=str(exc))

    return DownloadResult(url=url, status_code=0, html=None, cache_hit=False, error="unreachable")


async def fetch_race_list_sub_html(date: str) -> DownloadResult:
    url = f"https://race.netkeiba.com/top/race_list_sub.html?kaisai_date={date}"
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as hx:
            resp = await hx.get(url)
        html = resp.content.decode("euc-jp", errors="replace") if resp.content else ""
        return DownloadResult(url=url, status_code=resp.status_code, html=html, cache_hit=False)
    except Exception as exc:
        return DownloadResult(url=url, status_code=0, html=None, cache_hit=False, error=str(exc))
