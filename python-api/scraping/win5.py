"""
WIN5 対象レース取得スクレイパー
netkeiba.com の WIN5 ページから 5 レースの race_id を取得する。
"""
from __future__ import annotations

import asyncio
import re
from typing import Optional

from scraping.constants import SCRAPE_PROXY_URL, get_random_headers, jitter_sleep, is_cloudflare_block

try:
    from app_config import logger  # type: ignore
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


WIN5_URL = "https://race.netkeiba.com/top/win5.html"


async def get_win5_race_ids(date: str, session=None) -> list[str]:
    """
    指定日の WIN5 対象 5 レースの race_id リストを返す。
    date: "YYYYMMDD" 形式
    スクレイプ失敗時は空リストを返す。
    """
    import aiohttp
    from bs4 import BeautifulSoup

    url = f"{WIN5_URL}?kaisai_date={date}"
    _kwargs: dict = {}
    if SCRAPE_PROXY_URL:
        _kwargs["proxy"] = SCRAPE_PROXY_URL

    _own_session = session is None
    if _own_session:
        _timeout = aiohttp.ClientTimeout(total=20, connect=8)
        _connector = aiohttp.TCPConnector(limit=1, force_close=True)
        session = aiohttp.ClientSession(
            headers=get_random_headers(),
            timeout=_timeout,
            connector=_connector,
        )

    race_ids: list[str] = []
    try:
        async with session.get(url, **_kwargs) as resp:
            if resp.status != 200:
                logger.warning(f"[WIN5] HTTP {resp.status}: {url}")
                return []
            content = await resp.read()
            if is_cloudflare_block(content):
                logger.warning("[WIN5] Cloudflare ブロック検知")
                return []
            html = content.decode("euc-jp", errors="replace")

        soup = BeautifulSoup(html, "html.parser")

        # race_id を href から抽出
        # パターン1: /race/XXXXXXXXXXXXXXXX/ 形式
        # パターン2: ?race_id=XXXXXXXXXXXXXXXX 形式
        _seen: set[str] = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            # /race/202604010108/ または ?race_id=202604010108
            m = re.search(r"race_id=(\d{12,16})", href) or re.search(r"/race/(\d{12,16})", href)
            if m:
                rid = m.group(1)
                if rid not in _seen:
                    _seen.add(rid)
                    race_ids.append(rid)
            if len(race_ids) >= 5:
                break

        # パターン3: data-race_id 属性
        if not race_ids:
            for el in soup.find_all(attrs={"data-race_id": True}):
                rid = el["data-race_id"]
                if rid not in _seen and re.match(r"^\d{12,16}$", rid):
                    _seen.add(rid)
                    race_ids.append(rid)
                if len(race_ids) >= 5:
                    break

        # パターン4: JavaScript 変数 "race_id":"XXXXXXXX" または raceid: 'XXXXXXXX'
        if not race_ids:
            for m in re.finditer(r'["\']race_id["\']\s*[=:]\s*["\'](\d{12,16})["\']', html):
                rid = m.group(1)
                if rid not in _seen:
                    _seen.add(rid)
                    race_ids.append(rid)
                if len(race_ids) >= 5:
                    break

        if race_ids:
            logger.info(f"[WIN5] {date} → {len(race_ids)} レース取得: {race_ids}")
        else:
            logger.warning(f"[WIN5] {date} — race_id が見つかりませんでした（WIN5 未実施日の可能性）")

    except asyncio.TimeoutError:
        logger.warning(f"[WIN5] タイムアウト: {url}")
    except Exception as e:
        logger.error(f"[WIN5] スクレイプエラー: {e}")
    finally:
        if _own_session:
            await session.close()

    return race_ids[:5]
