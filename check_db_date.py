"""今日（20260509）のレースIDをdb.netkeiba.comとrace.netkeiba.comから確認する"""
import sys
import asyncio
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "python-api"))

date_str = "20260509"


async def main():
    import httpx
    from bs4 import BeautifulSoup

    # 1. db.netkeiba.com/race/list/{date}/
    url1 = f"https://db.netkeiba.com/race/list/{date_str}/"
    print(f"\n=== {url1} ===")
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True,
                                     headers={"User-Agent": "Mozilla/5.0"}) as c:
            r = await c.get(url1)
        html1 = r.content.decode("euc-jp", errors="replace")
        ids1 = list(dict.fromkeys(re.findall(r"/race/(\d{12})/", html1)))
        print(f"HTTP {r.status_code}, race_ids: {len(ids1)}件 {ids1[:5]}")
        if not ids1:
            # 12桁数字パターンを探す
            nums = re.findall(r"\b(\d{12})\b", html1)
            print(f"12桁数字パターン: {list(dict.fromkeys(nums))[:5]}")
            print(f"HTMLサンプル(500chars): {html1[500:800]!r}")
    except Exception as e:
        print(f"失敗: {e}")

    # 2. race.netkeiba.com/top/race_list.html
    url2 = f"https://race.netkeiba.com/top/race_list.html?kaisai_date={date_str}"
    print(f"\n=== {url2} ===")
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True,
                                     headers={"User-Agent": "Mozilla/5.0"}) as c:
            r = await c.get(url2)
        html2 = r.content.decode("euc-jp", errors="replace")
        ids2 = list(dict.fromkeys(re.findall(r"race_id=(\d{12})", html2)))
        print(f"HTTP {r.status_code}, race_id= 形式: {len(ids2)}件 {ids2[:5]}")
        ids2b = list(dict.fromkeys(re.findall(r"/race/(\d{12})/", html2)))
        print(f"/race/ 形式: {len(ids2b)}件 {ids2b[:5]}")
        # 12桁の数字を探す
        nums2 = list(dict.fromkeys(re.findall(r"\b(\d{12})\b", html2)))
        print(f"12桁数字: {nums2[:5]}")
        # リンクの一部を表示
        soup = BeautifulSoup(html2, "lxml")
        links = [a["href"] for a in soup.find_all("a", href=True) if "race" in a.get("href","").lower()]
        print(f"race含むリンク: {links[:5]}")
    except Exception as e:
        print(f"失敗: {e}")


    # 3. race_list_sub.html（元のフォールバック）
    url3 = f"https://race.netkeiba.com/top/race_list_sub.html?kaisai_date={date_str}"
    print(f"\n=== {url3} ===")
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True,
                                     headers={"User-Agent": "Mozilla/5.0"}) as c:
            r = await c.get(url3)
        # EUC-JPでデコード
        html3 = r.content.decode("euc-jp", errors="replace")
        ids3 = list(dict.fromkeys(re.findall(r"race_id=(\d{12})", html3)))
        print(f"HTTP {r.status_code}, race_id= 形式: {len(ids3)}件 {ids3[:5]}")
        nums3 = list(dict.fromkeys(re.findall(r"\b(\d{12})\b", html3)))
        print(f"12桁数字: {nums3[:5]}")
        print(f"HTMLサンプル: {html3[:500]!r}")
    except Exception as e:
        print(f"失敗: {e}")


asyncio.run(main())


