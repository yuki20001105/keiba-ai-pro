"""
スクレイピング検証スクリプト
1. DBデータの充足率チェック
2. HTMLパースロジックのユニットテスト（モック）
3. ネットワーク接続テスト（CF ブロック検知）
"""
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

# Windows CP932 環境でも出力できるよう UTF-8 に設定
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "keiba" / "data" / "keiba_ultimate.db"
sys.path.insert(0, str(BASE_DIR / "python-api"))

# ============================================================
# 1. DB 充足率チェック
# ============================================================

def check_db_quality():
    if not DB_PATH.exists():
        print(f"[ERROR] DB が見つかりません: {DB_PATH}")
        return

    conn = sqlite3.connect(str(DB_PATH))
    races = conn.execute("SELECT COUNT(*) FROM races_ultimate").fetchone()[0]
    results = conn.execute("SELECT COUNT(*) FROM race_results_ultimate").fetchone()[0]
    print(f"\n{'='*60}")
    print(f"1. DB データ充足率チェック")
    print(f"{'='*60}")
    print(f"  races_ultimate      : {races} 件")
    print(f"  race_results_ultimate: {results} 件")
    print(f"  平均頭数             : {results/races:.1f} 頭/レース" if races else "")

    # races_ultimate フィールド充足率
    race_rows = conn.execute("SELECT race_id, data FROM races_ultimate").fetchall()
    race_fields = ["date", "venue", "race_class", "distance", "track_type", "course_direction",
                   "weather", "field_condition", "post_time"]
    race_missing = {f: 0 for f in race_fields}
    for _, raw in race_rows:
        d = json.loads(raw)
        for f in race_fields:
            if not d.get(f):
                race_missing[f] += 1

    print(f"\n  races_ultimate フィールド充足率 ({races}件):")
    for f, cnt in race_missing.items():
        pct = 100.0 * (races - cnt) / races if races else 0
        mark = "✓" if pct >= 95 else ("△" if pct >= 70 else "✗")
        print(f"    {mark} {f:<20} : {pct:5.1f}% ({cnt}件欠損)")

    # race_results_ultimate フィールド充足率
    res_rows = conn.execute("SELECT race_id, data FROM race_results_ultimate").fetchall()
    res_fields = ["horse_name", "horse_id", "jockey_name", "jockey_id", "trainer_name",
                  "finish_position", "odds", "popularity", "weight_kg", "last_3f",
                  "sire", "horse_birth_date"]
    res_missing = {f: 0 for f in res_fields}
    for _, raw in res_rows:
        d = json.loads(raw)
        for f in res_fields:
            if d.get(f) is None or d.get(f) == "":
                res_missing[f] += 1

    print(f"\n  race_results_ultimate フィールド充足率 ({results}件):")
    for f, cnt in res_missing.items():
        pct = 100.0 * (results - cnt) / results if results else 0
        mark = "✓" if pct >= 95 else ("△" if pct >= 70 else "✗")
        print(f"    {mark} {f:<22} : {pct:5.1f}% ({cnt}件欠損)")

    # trainer_name 欠損の集中度チェック
    trainer_miss_races = {}
    for rid, raw in res_rows:
        d = json.loads(raw)
        if not d.get("trainer_name"):
            trainer_miss_races[rid] = trainer_miss_races.get(rid, 0) + 1
    if trainer_miss_races:
        print(f"\n  trainer_name 欠損: {len(trainer_miss_races)} レースに集中")
        for rid, cnt in sorted(trainer_miss_races.items(), key=lambda x: -x[1])[:5]:
            print(f"    {rid}: {cnt}頭")

    # race_class 欠損サンプル
    missing_class_samples = []
    for rid, raw in race_rows:
        d = json.loads(raw)
        if not d.get("race_class"):
            missing_class_samples.append((rid, d.get("race_name", ""), d.get("distance", 0)))
    if missing_class_samples:
        print(f"\n  race_class 欠損サンプル ({len(missing_class_samples)}件):")
        for rid, name, dist in missing_class_samples[:8]:
            print(f"    {rid}: {name!r} {dist}m")

    conn.close()


# ============================================================
# 2. HTML パースロジック ユニットテスト（モック HTML）
# ============================================================

MOCK_RACE_HTML = """<!DOCTYPE html>
<html><head><title>2024年1月6日 中山 1R レース詳細</title></head>
<body>
<h1>3歳未勝利</h1>
<div class="mainrace_data">
  <p class="smalltxt">2024年1月6日 1回中山1日目 第1R</p>
  <p>芝右1200m 天候:晴 芝:良 発走:10:00</p>
</div>
<table class="race_table_01">
<tr>
  <th>着順</th><th>枠番</th><th>馬番</th><th>馬名</th><th>性齢</th>
  <th>斤量</th><th>騎手</th><th>タイム</th><th>着差</th><th>単勝</th>
  <th>人気</th><th>通過</th><th>上り</th><th>馬体重</th><th>調教師</th><th>賞金</th>
</tr>
<tr>
  <td>1</td><td>3</td><td>5</td>
  <td><a href="/horse/2021100001/">テストバ</a></td>
  <td>牡3</td><td>56.0</td>
  <td><a href="/jockey/01234/">テスト騎手</a></td>
  <td>1:12.5</td><td></td><td>3.5</td>
  <td>2</td><td>4-4</td><td>34.1</td><td>478(+2)</td>
  <td><a href="/trainer/01234/">テスト調教師</a></td><td>51.0</td>
</tr>
<tr>
  <td>2</td><td>1</td><td>2</td>
  <td><a href="/horse/2021100002/">テストバ2</a></td>
  <td>牝3</td><td>54.0</td>
  <td><a href="/jockey/01235/">テスト騎手2</a></td>
  <td>1:12.8</td><td>クビ</td><td>1.8</td>
  <td>1</td><td>2-2</td><td>34.5</td><td>450(-4)</td>
  <td><a href="/trainer/01235/">テスト調教師2</a></td><td>20.4</td>
</tr>
</table>
</body></html>"""


def test_parse_logic():
    print(f"\n{'='*60}")
    print(f"2. HTMLパースロジック ユニットテスト")
    print(f"{'='*60}")

    try:
        import re
        from bs4 import BeautifulSoup
        from scraping.constants import HTML_STRAINER, VENUE_MAP

        soup = BeautifulSoup(MOCK_RACE_HTML, "lxml", parse_only=HTML_STRAINER)
        race_id = "202406010101"

        # 日付抽出
        smalltxt_p = soup.find("p", class_="smalltxt")
        date_str = ""
        if smalltxt_p:
            stxt = smalltxt_p.get_text()
            sdm = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", stxt)
            if sdm:
                date_str = f"{sdm.group(1)}{int(sdm.group(2)):02d}{int(sdm.group(3)):02d}"
        print(f"  日付抽出: {date_str!r} {'✓' if date_str == '20240106' else '✗'}")

        # venue
        venue = VENUE_MAP.get(race_id[4:6], race_id[4:6])
        print(f"  会場抽出: {venue!r} {'✓' if venue == '中山' else '✗'}")

        # 距離・トラック
        mainrace = soup.find("div", class_="mainrace_data")
        info_text = mainrace.get_text(" ") if mainrace else ""
        dist_m = re.search(r"(芝|ダ)[右左直外内障]{0,3}\s*(\d+)[mｍ]", info_text)
        track_type = ("芝" if dist_m.group(1) == "芝" else "ダート") if dist_m else ""
        distance = int(dist_m.group(2)) if dist_m else 0
        print(f"  距離抽出: {distance}m {track_type} {'✓' if distance == 1200 and track_type == '芝' else '✗'}")

        # テーブル解析
        table = soup.find("table", class_="race_table_01")
        all_rows = table.find_all("tr")
        header_texts = [c.get_text(strip=True) for c in all_rows[0].find_all(["th", "td"])]

        def col_idx(names, default=-1):
            for name in names:
                for i, h in enumerate(header_texts):
                    if name in h:
                        return i
            return default

        IDX_HORSE = col_idx(["馬名"], 3)
        IDX_JOCKEY = col_idx(["騎手"], 6)
        IDX_TIME = col_idx(["タイム"], 7)
        IDX_ODDS = col_idx(["単勝"], -1)
        IDX_WEIGHT = col_idx(["馬体重"], -1)
        IDX_TRAINER = col_idx(["調教師"], -1)

        print(f"  列インデックス検出: 馬名={IDX_HORSE} 騎手={IDX_JOCKEY} タイム={IDX_TIME} "
              f"単勝={IDX_ODDS} 馬体重={IDX_WEIGHT} 調教師={IDX_TRAINER}")
        idx_ok = (IDX_HORSE == 3 and IDX_JOCKEY == 6 and IDX_TIME == 7 and
                  IDX_ODDS == 9 and IDX_WEIGHT == 13 and IDX_TRAINER == 14)
        print(f"  列インデックス全検出: {'✓' if idx_ok else '✗'}")

        # 1行目の馬データ
        cols1 = all_rows[1].find_all("td")
        horse_name = cols1[IDX_HORSE].find("a").get_text(strip=True) if IDX_HORSE < len(cols1) else ""
        jockey_name = cols1[IDX_JOCKEY].find("a").get_text(strip=True) if IDX_JOCKEY < len(cols1) else ""
        weight_text = cols1[IDX_WEIGHT].get_text(strip=True) if IDX_WEIGHT < len(cols1) else ""
        wm = re.match(r"(\d+)\(([+-]?\d+)\)", weight_text)
        weight_kg = int(wm.group(1)) if wm else None
        weight_change = int(wm.group(2)) if wm else None

        print(f"  1着 馬名: {horse_name!r} {'✓' if horse_name == 'テストバ' else '✗'}")
        print(f"  1着 騎手: {jockey_name!r} {'✓' if jockey_name == 'テスト騎手' else '✗'}")
        print(f"  1着 馬体重: {weight_kg}kg ({weight_change:+d}) {'✓' if weight_kg == 478 and weight_change == 2 else '✗'}")

        print(f"\n  → パースロジック: 全テスト {'✓ PASS' if idx_ok and horse_name == 'テストバ' and weight_kg == 478 else '✗ FAIL'}")
    except Exception as e:
        import traceback
        print(f"  [ERROR] {e}")
        traceback.print_exc()


# ============================================================
# 3. ネットワーク接続テスト（CF ブロック検知）
# ============================================================

async def test_network():
    print(f"\n{'='*60}")
    print(f"3. ネットワーク接続テスト")
    print(f"{'='*60}")
    try:
        import aiohttp
        from scraping.constants import SCRAPE_HEADERS, SCRAPE_PROXY_URL, is_cloudflare_block

        print(f"  SCRAPE_PROXY_URL: {SCRAPE_PROXY_URL or '(未設定)'}")

        test_urls = [
            ("https://db.netkeiba.com/", "netkeiba トップ"),
            ("https://db.netkeiba.com/race/list/20250101/", "2025/01/01 レース一覧"),
        ]

        timeout = aiohttp.ClientTimeout(total=10, connect=5)
        async with aiohttp.ClientSession(headers=SCRAPE_HEADERS, timeout=timeout) as session:
            for url, label in test_urls:
                try:
                    async with session.get(url) as resp:
                        content = await resp.read()
                        blocked = is_cloudflare_block(content)
                        status_str = f"HTTP {resp.status}"
                        if resp.status == 200 and not blocked:
                            result = f"✓ 正常 ({len(content)}B)"
                        elif blocked:
                            result = f"✗ Cloudflare ブロック ({len(content)}B)"
                        else:
                            result = f"✗ {status_str} ({len(content)}B)"
                        print(f"  {label}: {result}")
                except asyncio.TimeoutError:
                    print(f"  {label}: ✗ タイムアウト")
                except Exception as e:
                    print(f"  {label}: ✗ エラー ({e})")

        if not SCRAPE_PROXY_URL:
            print("\n  ⚠️  IP ブロックされている場合は環境変数 SCRAPE_PROXY_URL を設定してください")
            print("     例: $env:SCRAPE_PROXY_URL='http://user:pass@proxy:8080'")
    except Exception as e:
        print(f"  [ERROR] {e}")


# ============================================================
# 4. ジョブDB 状態確認
# ============================================================

def check_job_db():
    job_db = BASE_DIR / "keiba" / "data" / "scrape_jobs.db"
    print(f"\n{'='*60}")
    print(f"4. ジョブ永続化DB 確認")
    print(f"{'='*60}")
    if not job_db.exists():
        print(f"  scrape_jobs.db: 未作成（まだジョブが実行されていない）")
    else:
        conn = sqlite3.connect(str(job_db))
        jobs = conn.execute("SELECT job_id, status, updated_at FROM scrape_jobs ORDER BY updated_at DESC LIMIT 10").fetchall()
        print(f"  scrape_jobs.db: {len(jobs)} 件")
        for jid, status, ts in jobs:
            print(f"    {jid}: {status} ({ts})")
        conn.close()

    ped_db = BASE_DIR / "keiba" / "data" / "pedigree_cache.db"
    print(f"\n  pedigree_cache.db: {'存在する' if ped_db.exists() else '未作成（まだスクレイプ未実施）'}")
    if ped_db.exists():
        conn = sqlite3.connect(str(ped_db))
        cnt = conn.execute("SELECT COUNT(*) FROM pedigree_cache").fetchone()[0]
        print(f"    キャッシュ件数: {cnt}件")
        conn.close()


# ============================================================
# main
# ============================================================

if __name__ == "__main__":
    check_db_quality()
    test_parse_logic()
    asyncio.run(test_network())
    check_job_db()
    print(f"\n{'='*60}")
    print("検証完了")
    print(f"{'='*60}\n")
