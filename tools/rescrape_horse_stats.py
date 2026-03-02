"""
P3-11: horse_total_runs / horse_total_wins / horse_total_prize_money 再スクレイプ
=============================================================================
IPブロック解除後に実行。3844件のうち horse_total_runs == 0 の通算成績を埋める。

使い方:
  python tools/rescrape_horse_stats.py [--proxy http://user:pass@host:port] [--limit 100] [--dry-run]

オプション:
  --proxy   IPブロック回避用プロキシ (例: http://proxy.example.com:8080)
  --limit   1回の実行で処理する馬の数上限 (デフォルト: 全件)
  --dry-run スクレイプせず対象一覧だけ表示
  --delay   リクエスト間隔 (秒, デフォルト: 2.0)
"""
import sys, os, asyncio, json, sqlite3, re, argparse, time
from pathlib import Path
from typing import Optional
from datetime import datetime

# パス設定
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "python-api"))
sys.path.insert(0, str(ROOT / "keiba"))
os.chdir(ROOT / "python-api")

DB_PATH = ROOT / "keiba" / "data" / "keiba_ultimate.db"
LOG_PATH = ROOT / "tools" / "rescrape_horse_stats.log"

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

try:
    import aiohttp
    from bs4 import BeautifulSoup
except ImportError:
    print("pip install aiohttp beautifulsoup4 lxml")
    sys.exit(1)


# ── 対象馬を取得 ─────────────────────────────────────────────────────────────
def get_missing_horses(db_path: Path) -> list[dict]:
    """horse_total_runs が 0 または NULL の horse_id を一覧取得"""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT race_id, data FROM race_results_ultimate")
    rows = cur.fetchall()
    conn.close()

    seen: dict[str, dict] = {}  # horse_id -> {horse_id, horse_name, count}
    for row in rows:
        d = json.loads(row["data"])
        hid = d.get("horse_id", "")
        if not hid:
            continue
        total_runs = d.get("horse_total_runs", 0) or 0
        if total_runs > 0:
            continue  # すでに取得済み
        if hid not in seen:
            seen[hid] = {"horse_id": hid,
                         "horse_name": d.get("horse_name", ""),
                         "count": 0}
        seen[hid]["count"] += 1

    result = sorted(seen.values(), key=lambda x: x["count"], reverse=True)
    log.info(f"対象馬: {len(result)} 頭 (horse_total_runsが0の馬)")
    return result


# ── 通算成績スクレイプ ──────────────────────────────────────────────────────
CAREER_RE  = re.compile(r'通算成績.*?(\d+)戦.*?(\d+)勝', re.S)
CAREER_RE2 = re.compile(r'(\d+)\s*戦\s*(\d+)\s*勝')
PRIZE_RE   = re.compile(r'総収得賞金[：:]\s*([\d,]+)\s*万円')
PRIZE_RE2  = re.compile(r'([\d,]+)\s*万円')

async def scrape_horse_career(session: "aiohttp.ClientSession", horse_id: str,
                               delay: float = 2.0) -> Optional[dict]:
    """netkeiba で horse_id の通算成績・賞金を取得"""
    # JRA: 数字ID → db.netkeiba.com/horse/<id>/
    # NAR: B prefix → netkeiba ではなく地方競馬DBなので skip
    if str(horse_id).startswith("B"):
        return None  # NARは通算成績ページが別サイトのためスキップ

    url = f"https://db.netkeiba.com/horse/{horse_id}/"
    await asyncio.sleep(delay)
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status != 200:
                log.warning(f"HTTP {resp.status}: {horse_id}")
                return None
            content = await resp.read()
            html = content.decode("euc-jp", errors="replace")
    except Exception as e:
        log.warning(f"取得失敗 {horse_id}: {e}")
        return None

    soup = BeautifulSoup(html, "lxml")

    # 通算成績
    total_runs, total_wins = 0, 0
    career_text = soup.get_text()
    m = CAREER_RE.search(career_text)
    if not m:
        m = CAREER_RE2.search(career_text)
    if m:
        total_runs = int(m.group(1))
        total_wins = int(m.group(2))

    # 賞金
    total_prize = 0.0
    pm = PRIZE_RE.search(career_text)
    if pm:
        total_prize = float(pm.group(1).replace(",", "")) * 10000
    else:
        # テーブルから探す
        for td in soup.find_all("td"):
            txt = td.get_text(strip=True)
            pm2 = PRIZE_RE2.search(txt)
            if pm2 and "賞金" in (td.find_previous("th") or td).get_text():
                total_prize = float(pm2.group(1).replace(",", "")) * 10000
                break

    if total_runs == 0:
        log.debug(f"通算成績取得失敗 {horse_id}: runs=0")
        return None

    log.info(f"  {horse_id}: {total_runs}戦{total_wins}勝 {total_prize/10000:.0f}万円")
    return {
        "horse_total_runs":         total_runs,
        "horse_total_wins":         total_wins,
        "horse_total_prize_money":  total_prize,
        "horse_win_rate":           round(total_wins / total_runs, 4) if total_runs else 0.0,
    }


# ── DB更新 ──────────────────────────────────────────────────────────────────
def update_horse_stats_in_db(db_path: Path, horse_id: str, stats: dict) -> int:
    """race_results_ultimate の該当 horse_id 全行をJSON更新、更新行数を返す"""
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT rowid, data FROM race_results_ultimate")
    rows = cur.fetchall()
    updated = 0
    for (rowid, data_json) in rows:
        d = json.loads(data_json)
        if d.get("horse_id") != horse_id:
            continue
        d.update(stats)
        cur.execute("UPDATE race_results_ultimate SET data=? WHERE rowid=?",
                    (json.dumps(d, ensure_ascii=False), rowid))
        updated += 1
    conn.commit()
    conn.close()
    return updated


# ── メイン ──────────────────────────────────────────────────────────────────
async def main(args):
    horses = get_missing_horses(DB_PATH)
    if args.limit:
        horses = horses[:args.limit]

    log.info(f"処理対象: {len(horses)} 頭  dry_run={args.dry_run}")

    if args.dry_run:
        print(f"\n{'horse_id':<20} {'horse_name':<16} {'rows':>5}")
        print("-" * 45)
        for h in horses[:50]:
            print(f"{h['horse_id']:<20} {h['horse_name']:<16} {h['count']:>5}")
        if len(horses) > 50:
            print(f"  ... 他 {len(horses)-50} 頭")
        return

    # aiohttp セッション設定
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ja,en;q=0.9",
    }
    connector_kwargs = {}
    if args.proxy:
        # aiohttp v3 はコネクタにプロキシ非対応、session.get に proxy=引数を渡す
        pass

    total_ok   = 0
    total_fail = 0
    start_time = time.time()

    async with aiohttp.ClientSession(headers=headers) as session:
        for i, h in enumerate(horses):
            horse_id   = h["horse_id"]
            horse_name = h["horse_name"]
            log.info(f"[{i+1}/{len(horses)}] {horse_id} {horse_name}")

            kwargs = {"delay": args.delay}
            if args.proxy:
                # aiohttp の proxy を session.get に渡す方式
                orig = session.get
                async def _get_with_proxy(url, **kw):
                    return await orig(url, proxy=args.proxy, **kw)
                session.get = _get_with_proxy  # type: ignore

            stats = await scrape_horse_career(session, horse_id, delay=args.delay)
            if args.proxy:
                session.get = orig  # type: ignore (restore)

            if not stats:
                total_fail += 1
                continue

            updated = update_horse_stats_in_db(DB_PATH, horse_id, stats)
            log.info(f"    DB更新: {updated}行")
            total_ok += 1

    elapsed = time.time() - start_time
    log.info(
        f"\n完了: {total_ok}頭成功 / {total_fail}頭失敗 / {len(horses)}頭対象"
        f"  経過:{elapsed:.0f}秒"
    )

    # 完了後にモデル再学習を促す
    if total_ok > 0:
        log.info(
            "\n次のステップ:\n"
            "  python tools/retrain_local.py\n"
            "  (horse_total_*/horse_win_rateをunnecessary_colsから削除してから実行)"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="horse career stats re-scraper")
    parser.add_argument("--proxy",   default=None,  help="プロキシURL (例: http://user:pass@host:port)")
    parser.add_argument("--limit",   type=int, default=None, help="処理上限馬数")
    parser.add_argument("--delay",   type=float, default=2.0, help="リクエスト間隔(秒)")
    parser.add_argument("--dry-run", action="store_true", help="対象一覧のみ表示")
    args = parser.parse_args()
    asyncio.run(main(args))
