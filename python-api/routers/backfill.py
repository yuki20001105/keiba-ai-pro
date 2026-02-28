"""
バックフィルエンドポイント
POST /api/backfill/nar-pedigree
POST /api/backfill/coat-color
"""
from __future__ import annotations

import asyncio
import json

import aiohttp
from bs4 import BeautifulSoup
from fastapi import APIRouter

from app_config import SUPABASE_ENABLED, get_supabase_client, logger  # type: ignore
from scraping.constants import SCRAPE_HEADERS, HTML_STRAINER  # type: ignore
from scraping.horse import _parse_blood_table, extract_coat_color  # type: ignore

router = APIRouter()


@router.post("/api/backfill/nar-pedigree")
async def backfill_nar_pedigree(limit: int = 100) -> dict:
    """
    Supabase の race_results_ultimate で sire='unknown_local' の NAR 馬（B プレフィックス）に対して
    db.netkeiba.com/horse/ped/<horse_id>/ から血統を再取得し、レコードを更新する。
    """
    if not SUPABASE_ENABLED:
        return {"success": False, "message": "Supabase 無効"}
    sb = get_supabase_client()
    if sb is None:
        return {"success": False, "message": "Supabase クライアント取得失敗"}

    # 1) unknown_local の行を収集
    offset = 0
    unknown_rows: list[dict] = []
    seen_horse_ids: set = set()
    while len(seen_horse_ids) < limit:
        res = sb.table("race_results_ultimate").select("id,race_id,data").range(offset, offset + 999).execute()
        if not res.data:
            break
        for r in res.data:
            d = r["data"] if isinstance(r["data"], dict) else json.loads(r["data"])
            hid = str(d.get("horse_id", "") or "")
            sire = str(d.get("sire", "") or "").strip()
            if hid.startswith("B") and sire in ("", "unknown_local"):
                unknown_rows.append({"row_id": r["id"], "race_id": r["race_id"], "data": d, "horse_id": hid})
                seen_horse_ids.add(hid)
        if len(res.data) < 1000:
            break
        offset += 1000

    logger.info(f"NAR血統バックフィル: sire空/unknown_local 行={len(unknown_rows)} ユニーク馬={len(seen_horse_ids)}")

    if not unknown_rows:
        return {"success": True, "message": "バックフィル対象なし", "updated": 0, "failed": 0}

    # 2) 馬ごとに /ped/ を取得
    timeout = aiohttp.ClientTimeout(total=30)
    connector = aiohttp.TCPConnector(limit=3, limit_per_host=2)
    pedigree_map: dict = {}

    unique_horse_ids = list(seen_horse_ids)[:limit]
    async with aiohttp.ClientSession(headers=SCRAPE_HEADERS, timeout=timeout, connector=connector) as session:
        for hid in unique_horse_ids:
            ped_url = f"https://db.netkeiba.com/horse/ped/{hid}/"
            for attempt in range(3):
                try:
                    await asyncio.sleep(0.5 + attempt * 1.5)
                    async with session.get(ped_url) as resp:
                        if resp.status == 200:
                            content = await resp.read()
                            html = content.decode("euc-jp", errors="ignore")
                            # SoupStrainer を使用してメモリ効率を改善（Bug #2 修正）
                            soup_p = BeautifulSoup(html, "lxml", parse_only=HTML_STRAINER)
                            bt = soup_p.find("table", class_="blood_table")
                            pedigree_map[hid] = {}
                            if bt:
                                _parse_blood_table(bt, pedigree_map[hid])
                            if pedigree_map[hid].get("sire"):
                                logger.info(f"  /ped/ 成功: {hid} sire={pedigree_map[hid]['sire']}")
                            else:
                                logger.debug(f"  /ped/ 200 だが blood_table 未取得: {hid}")
                            break
                        elif resp.status == 429:
                            await asyncio.sleep(5.0 + attempt * 3.0)
                            continue
                        else:
                            logger.debug(f"  /ped/ HTTP {resp.status}: {hid}")
                            break
                except Exception as e:
                    logger.debug(f"  /ped/ エラー 試行{attempt+1} {hid}: {e}")
                    if attempt < 2:
                        await asyncio.sleep(2.0 ** attempt)

    # 3) 取得成功した馬の Supabase レコードを更新
    updated, failed = 0, 0
    for row in unknown_rows:
        hid = row["horse_id"]
        ped = pedigree_map.get(hid, {})
        sire = ped.get("sire", "")
        if not sire:
            failed += 1
            continue
        new_data = dict(row["data"])
        new_data.update({"sire": ped.get("sire", ""), "dam": ped.get("dam", ""), "damsire": ped.get("damsire", "")})
        try:
            sb.table("race_results_ultimate").update({"data": new_data}).eq("id", row["row_id"]).execute()
            updated += 1
            try:
                from app_config import save_pedigree_cache  # type: ignore
                await asyncio.to_thread(save_pedigree_cache, hid, ped.get("sire", ""), ped.get("dam", ""), ped.get("damsire", ""))
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"Supabase 更新失敗: row_id={row['row_id']} {e}")
            failed += 1

    return {
        "success": True,
        "message": f"{updated}件更新、{failed}件失敗",
        "updated": updated,
        "failed": failed,
        "total_unique_horses": len(unique_horse_ids),
        "total_rows_scanned": len(unknown_rows),
    }


@router.post("/api/backfill/coat-color")
async def backfill_coat_color(limit: int = 200) -> dict:
    """
    race_results_ultimate で horse_coat_color が未取得の馬について
    db.netkeiba.com/horse/<horse_id>/ を再スクレイプして毛色を補完する。
    """
    if not SUPABASE_ENABLED:
        return {"success": False, "message": "Supabase 無効"}
    sb = get_supabase_client()
    if sb is None:
        return {"success": False, "message": "Supabase クライアント取得失敗"}

    # 1) coat_color 欠損行を収集
    offset = 0
    target_rows: list[dict] = []
    seen_horse_ids: set = set()

    while len(seen_horse_ids) < limit:
        res = sb.table("race_results_ultimate").select("id,race_id,data").range(offset, offset + 999).execute()
        if not res.data:
            break
        for r in res.data:
            d = r["data"] if isinstance(r["data"], dict) else json.loads(r["data"])
            hid = str(d.get("horse_id", "") or "")
            coat = str(d.get("horse_coat_color", "") or "").strip()
            hurl = str(d.get("horse_url", "") or "")
            if hid and not coat:
                target_rows.append({
                    "row_id": r["id"], "race_id": r["race_id"],
                    "data": d, "horse_id": hid, "horse_url": hurl,
                    "is_nar": hid.startswith("B"),
                })
                seen_horse_ids.add(hid)
        if len(res.data) < 1000:
            break
        offset += 1000

    logger.info(f"coat_color バックフィル: 欠損行={len(target_rows)} ユニーク馬={len(seen_horse_ids)}")

    if not target_rows:
        return {"success": True, "message": "バックフィル対象なし", "updated": 0, "failed": 0}

    # 2) 馬ごとに detail ページから coat_color を取得
    timeout = aiohttp.ClientTimeout(total=30)
    connector = aiohttp.TCPConnector(limit=3, limit_per_host=2)
    coat_map: dict = {}

    unique_horse_ids = list(seen_horse_ids)[:limit]
    is_nar_map = {r["horse_id"]: r.get("is_nar", False) for r in target_rows}
    url_map = {r["horse_id"]: r["horse_url"] for r in target_rows}

    def _coat_urls(hid: str, hurl: str, is_nar: bool) -> list:
        if is_nar:
            return [
                f"https://db.sp.netkeiba.com/horse/{hid}/",
                f"https://db.sp.netkeiba.com/horse/result/{hid}/",
            ]
        pc = hurl if hurl.startswith("http") else f"https://db.netkeiba.com/horse/{hid}/"
        if not pc.endswith("/"):
            pc += "/"
        return [pc, f"https://db.sp.netkeiba.com/horse/{hid}/", f"https://db.sp.netkeiba.com/horse/result/{hid}/"]

    async with aiohttp.ClientSession(headers=SCRAPE_HEADERS, timeout=timeout, connector=connector) as session:
        for hid in unique_horse_ids:
            hurl = url_map.get(hid, "")
            is_nar = is_nar_map.get(hid, False)
            urls_to_try = _coat_urls(hid, hurl, is_nar)
            coat = ""
            for url in urls_to_try:
                for attempt in range(2):
                    try:
                        await asyncio.sleep(0.4 + attempt * 1.5)
                        async with session.get(url) as resp:
                            if resp.status == 200:
                                content = await resp.read()
                                html = content.decode("euc-jp", errors="ignore")
                                # SoupStrainer を使用してメモリ効率を改善（Bug #2 修正）
                                soup_h = BeautifulSoup(html, "lxml", parse_only=HTML_STRAINER)
                                coat = extract_coat_color(soup_h, html)
                                if coat:
                                    logger.info(f"  coat_color 取得: {hid} → {coat} ({url})")
                                else:
                                    logger.debug(f"  coat_color 未取得: {hid} ({url})")
                                break
                            elif resp.status == 429:
                                await asyncio.sleep(5.0 + attempt * 3.0)
                                continue
                            else:
                                logger.debug(f"  coat_color HTTP {resp.status}: {hid} ({url})")
                                break
                    except Exception as e:
                        logger.debug(f"  coat_color エラー 試行{attempt+1} {hid}: {e}")
                        if attempt < 1:
                            await asyncio.sleep(2.0)
                if coat:
                    break
            coat_map[hid] = coat

    # 3) 取得できた horse_id の全行を Supabase 更新
    updated, failed, skipped = 0, 0, 0
    for row in target_rows:
        hid = row["horse_id"]
        coat = coat_map.get(hid, "")
        if not coat:
            skipped += 1
            continue
        new_data = dict(row["data"])
        new_data["horse_coat_color"] = coat
        try:
            sb.table("race_results_ultimate").update({"data": new_data}).eq("id", row["row_id"]).execute()
            updated += 1
        except Exception as e:
            logger.warning(f"Supabase 更新失敗: row_id={row['row_id']} {e}")
            failed += 1

    return {
        "success": True,
        "message": f"{updated}件更新、{failed}件失敗、{skipped}件coat_color未取得",
        "updated": updated, "failed": failed, "skipped": skipped,
        "total_unique_horses": len(unique_horse_ids),
    }
