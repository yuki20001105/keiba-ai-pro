"""
リアルタイムオッズ取得エンドポイント
GET  /api/realtime-odds/{race_id}   - 単レース最新オッズ（単勝・馬連・三連複）
POST /api/realtime-odds/refresh     - 複数レース一括更新（最大10レース）

出典: race.netkeiba.com/odds/index.html?race_id=...
レース締切前のみ有効（締切済みは netkeiba が 302 → 結果ページへリダイレクト）
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import aiohttp
from fastapi import APIRouter

from app_config import logger  # type: ignore
from scraping.constants import SCRAPE_HEADERS  # type: ignore

router = APIRouter()

# ── インメモリキャッシュ（当日レースは 5 分 TTL / 旧レースは 60 秒 TTL）
_ODDS_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 300.0  # 秒（analyze_race → キャッシュ → オッズ更新ボタン GET の間が最大 5 分以内であれば再取得不要）


def _cached(race_id: str) -> dict | None:
    if race_id in _ODDS_CACHE:
        ts, data = _ODDS_CACHE[race_id]
        if time.time() - ts < _CACHE_TTL:
            return data
    return None


def _store(race_id: str, data: dict) -> None:
    _ODDS_CACHE[race_id] = (time.time(), data)
    # 古いエントリを掃除（最大200件）
    if len(_ODDS_CACHE) > 200:
        oldest = sorted(_ODDS_CACHE.items(), key=lambda x: x[1][0])[:50]
        for k, _ in oldest:
            del _ODDS_CACHE[k]


async def _fetch_tansho_odds(session: aiohttp.ClientSession, race_id: str) -> dict[str, float]:
    """単勝オッズを取得: race.netkeiba.com/odds/index.html
    netkeiba は JavaScript でオッズを動的ロードするため静的 HTML では ---.- となる場合が多い。
    静的 HTML で取得できなかった場合は _fetch_tansho_odds_playwright を使うこと。
    """
    url = f"https://race.netkeiba.com/odds/index.html?type=b1&race_id={race_id}"
    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                logger.warning(f"[odds] 単勝 HTTP {resp.status}: {race_id}")
                return {}
            html = (await resp.read()).decode("euc-jp", errors="replace")
        # 新フォーマット: <span id="odds-1_01">3.5</span>（2024年以降の netkeiba）
        pattern = re.compile(r'id="odds-1_(\d+)"[^>]*>([0-9.]+)<', re.IGNORECASE)
        result = {str(int(m.group(1))): float(m.group(2)) for m in pattern.finditer(html)}
        if result:
            return result
        # 旧フォーマット: <td id="odds_dl_b1_1">3.5</td>（フォールバック）
        pattern_old = re.compile(r'id="odds_dl_b1_(\d+)"[^>]*>([0-9.]+)<', re.IGNORECASE)
        return {m.group(1): float(m.group(2)) for m in pattern_old.finditer(html)}
    except Exception as e:
        logger.warning(f"[odds] 単勝取得失敗 {race_id}: {e}")
        return {}


async def _fetch_tansho_odds_playwright(race_id: str) -> dict[str, float]:
    """Playwright（ヘッドレスブラウザ）で JavaScript 実行後の単勝オッズを取得する。
    netkeiba のオッズは JavaScript AJAX でロードされるため、静的 HTML では ---.- のまま。
    静的 HTML 取得が失敗したレースの当日オッズ補完に使用すること。
    """
    try:
        from playwright.async_api import async_playwright  # type: ignore
        url = f"https://race.netkeiba.com/odds/index.html?type=b1&race_id={race_id}"
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                # JavaScript がオッズを ---.- から実値に置換するまで最大 8 秒待機
                try:
                    await page.wait_for_function(
                        "() => { const s=document.querySelectorAll('span.Odds'); "
                        "return s.length > 0 && !Array.from(s).every(x => x.textContent.includes('---')); }",
                        timeout=8_000,
                    )
                except Exception:
                    pass  # タイムアウト時はそのまま進む（---.- のまま → 空 dict 返却）
                html = await page.content()
            finally:
                await browser.close()
        pattern = re.compile(r'id="odds-1_(\d+)"[^>]*>([0-9.]+)<')
        result = {str(int(m.group(1))): float(m.group(2)) for m in pattern.finditer(html)}
        if result:
            logger.info(f"[odds_playwright] {race_id}: {len(result)} 頭のオッズ取得成功")
        else:
            logger.info(f"[odds_playwright] {race_id}: オッズ未公開（---.-）")
        return result
    except Exception as e:
        logger.warning(f"[odds_playwright] {race_id}: 取得失敗 {e}")
        return {}


_PW_ODDS_SEMAPHORE: asyncio.Semaphore | None = None
_PW_ODDS_SEMAPHORE_LIMIT = 3  # 同時 Playwright ページ数の上限


def _get_pw_semaphore() -> asyncio.Semaphore:
    global _PW_ODDS_SEMAPHORE
    if _PW_ODDS_SEMAPHORE is None:
        _PW_ODDS_SEMAPHORE = asyncio.Semaphore(_PW_ODDS_SEMAPHORE_LIMIT)
    return _PW_ODDS_SEMAPHORE


async def _fetch_tansho_odds_playwright_batch(
    race_ids: list[str],
) -> dict[str, dict[str, float]]:
    """複数レースの単勝オッズを Playwright で一括取得（ブラウザ 1 インスタンス・最大 3 並列ページ）。
    ブラウザの起動/終了コストを 1 回にまとめることで、36 レースを ~60 秒以内で処理できる。
    INV-07 に準拠: ページ間に 1.0 秒スリープを挟む。
    """
    if not race_ids:
        return {}
    try:
        from playwright.async_api import async_playwright  # type: ignore
        pattern = re.compile(r'id="odds-1_(\d+)"[^>]*>([0-9.]+)<')
        sem = _get_pw_semaphore()
        results: dict[str, dict[str, float]] = {}

        async def _fetch_one(browser: Any, race_id: str) -> None:
            async with sem:
                try:
                    page = await browser.new_page()
                    url = f"https://race.netkeiba.com/odds/index.html?type=b1&race_id={race_id}"
                    await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                    try:
                        await page.wait_for_function(
                            "() => { const s=document.querySelectorAll('span.Odds'); "
                            "return s.length > 0 && !Array.from(s).every(x => x.textContent.includes('---')); }",
                            timeout=8_000,
                        )
                    except Exception:
                        pass
                    html = await page.content()
                    await page.close()
                    r = {str(int(m.group(1))): float(m.group(2)) for m in pattern.finditer(html)}
                    results[race_id] = r
                    if r:
                        logger.info(f"[odds_pw_batch] {race_id}: {len(r)} 頭")
                    else:
                        logger.info(f"[odds_pw_batch] {race_id}: ---.-（未公開）")
                except Exception as e:
                    logger.warning(f"[odds_pw_batch] {race_id}: {e}")
                    results[race_id] = {}
                await asyncio.sleep(1.0)  # INV-07 スクレイピングインターバル

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                await asyncio.gather(*[_fetch_one(browser, rid) for rid in race_ids])
            finally:
                await browser.close()
        return results
    except Exception as e:
        logger.warning(f"[odds_pw_batch] 一括取得失敗: {e}")
        return {}


async def _fetch_umaren_odds(session: aiohttp.ClientSession, race_id: str) -> dict[str, float]:
    """馬連オッズを取得: race.netkeiba.com/odds/index.html?type=b6"""
    url = f"https://race.netkeiba.com/odds/index.html?type=b6&race_id={race_id}"
    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                return {}
            html = (await resp.read()).decode("euc-jp", errors="replace")
        # <span id="odds_b6_1_2">12.5</span>
        pattern = re.compile(r'id="odds_b6_(\d+)_(\d+)"[^>]*>([0-9.]+)<')
        result: dict[str, float] = {}
        for m in pattern.finditer(html):
            key = f"{m.group(1)}-{m.group(2)}"
            result[key] = float(m.group(3))
        return result
    except Exception as e:
        logger.warning(f"[odds] 馬連取得失敗 {race_id}: {e}")
        return {}


async def _fetch_sanrenpuku_odds(session: aiohttp.ClientSession, race_id: str) -> dict[str, float]:
    """三連複オッズ（上位50組のみ）を取得"""
    url = f"https://race.netkeiba.com/odds/index.html?type=b8&race_id={race_id}"
    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                return {}
            html = (await resp.read()).decode("euc-jp", errors="replace")
        pattern = re.compile(r'id="odds_b8_(\d+)_(\d+)_(\d+)"[^>]*>([0-9.]+)<')
        result: dict[str, float] = {}
        for m in pattern.finditer(html):
            key = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            result[key] = float(m.group(4))
        return result
    except Exception as e:
        logger.warning(f"[odds] 三連複取得失敗 {race_id}: {e}")
        return {}


async def _scrape_odds(race_id: str, bet_types: list[str]) -> dict[str, Any]:
    """指定馬券種のオッズを並列取得して返す"""
    timeout = aiohttp.ClientTimeout(total=8, connect=4)
    headers = {**SCRAPE_HEADERS, "Referer": f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"}
    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        tasks: dict[str, Any] = {}
        if "tansho" in bet_types:
            tasks["tansho"] = _fetch_tansho_odds(session, race_id)
        if "umaren" in bet_types:
            tasks["umaren"] = _fetch_umaren_odds(session, race_id)
        if "sanrenpuku" in bet_types:
            tasks["sanrenpuku"] = _fetch_sanrenpuku_odds(session, race_id)

        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        data: dict[str, Any] = {"race_id": race_id, "fetched_at": time.time(), "odds": {}}
        for key, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                logger.warning(f"[odds] {key} 取得例外 {race_id}: {result}")
                data["odds"][key] = {}
            else:
                data["odds"][key] = result

        # 静的 HTML でオッズが取れなかった場合（netkeiba は JS AJAX で動的ロード）
        # Playwright（ヘッドレスブラウザ）でフォールバック取得
        if "tansho" in bet_types and not data["odds"].get("tansho"):
            pw_odds = await _fetch_tansho_odds_playwright(race_id)
            if pw_odds:
                data["odds"]["tansho"] = pw_odds

        data["horse_count"] = len(data["odds"].get("tansho", {}))
        return data


@router.get("/api/realtime-odds/{race_id}")
async def get_realtime_odds(race_id: str, types: str = "tansho,umaren"):
    """
    指定レースの最新オッズを取得（60秒キャッシュ）。
    types: カンマ区切りで tansho / umaren / sanrenpuku を指定
    """
    cached = _cached(race_id)
    if cached:
        return {**cached, "cache_hit": True}

    bet_types = [t.strip() for t in types.split(",") if t.strip()]
    if not bet_types:
        bet_types = ["tansho"]

    data = await _scrape_odds(race_id, bet_types)
    _store(race_id, data)
    return {**data, "cache_hit": False}


@router.post("/api/realtime-odds/refresh")
async def refresh_realtime_odds(body: dict):
    """
    複数レースのオッズを一括更新（最大36レース）。
    body: { "race_ids": ["202605051211", ...], "types": "tansho,umaren" }

    フロー:
    1. キャッシュヒットしているレースはスキップ
    2. 静的 HTML で取得できたレースはそのままキャッシュ
    3. 静的 HTML で ---.- だったレースは Playwright バッチ取得（3 並列・ブラウザ共有）
    """
    from datetime import datetime as _dt
    _today_str = _dt.now().strftime("%Y%m%d")

    race_ids: list[str] = body.get("race_ids", [])[:36]
    bet_types_str: str = body.get("types", "tansho,umaren")
    bet_types = [t.strip() for t in bet_types_str.split(",") if t.strip()]

    results: dict[str, Any] = {}
    pw_needed: list[str] = []  # 静的 HTML でオッズが取れなかった当日レース

    for race_id in race_ids:
        # キャッシュヒット → スキップ
        if _cached(race_id):
            results[race_id] = {"success": True, "horse_count": len(_cached(race_id)["odds"].get("tansho", {})), "cache": True}  # type: ignore[index]
            continue
        # 過去レース（昨日以前）はリアルタイムオッズ不可 → スキップ
        race_date = race_id[:8] if len(race_id) >= 8 else ""
        if race_date and race_date < _today_str:
            results[race_id] = {"success": False, "error": "past_race", "skipped": True}
            continue
        try:
            data = await _scrape_odds(race_id, bet_types)
            _store(race_id, data)
            if data["horse_count"] > 0:
                results[race_id] = {"success": True, "horse_count": data["horse_count"]}
            else:
                # 静的 HTML ではオッズ未取得 → Playwright バッチ待ち
                pw_needed.append(race_id)
                results[race_id] = {"success": False, "pending_playwright": True}
        except Exception as e:
            results[race_id] = {"success": False, "error": str(e)}
        await asyncio.sleep(0.5)

    # Playwright バッチ取得（当日レースで静的 HTML に失敗したもの）
    if pw_needed:
        logger.info(f"[odds_refresh] Playwright バッチ取得: {len(pw_needed)} レース")
        pw_results = await _fetch_tansho_odds_playwright_batch(pw_needed)
        for race_id, tansho in pw_results.items():
            data = {
                "race_id": race_id,
                "fetched_at": time.time(),
                "odds": {"tansho": tansho},
                "horse_count": len(tansho),
            }
            _store(race_id, data)
            results[race_id] = {"success": True, "horse_count": len(tansho), "via_playwright": True}

    return {"results": results, "refreshed": len(race_ids)}
