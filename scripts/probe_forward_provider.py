"""Bounded real-provider GET evidence; never a prospective capture or a bet."""
import argparse
import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba")]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-network", action="store_true")
    args = parser.parse_args()
    from urllib.parse import urlsplit
    link = urlsplit(args.url)
    if not args.allow_network or link.scheme != "https" or link.hostname != "race.netkeiba.com" or link.port not in (None, 443) or link.username:
        parser.error("Explicit network permission and official race HTTPS URL required")
    import aiohttp
    from scraping.fetch_pipeline import fetch_text
    started = datetime.now(timezone.utc).isoformat()
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as session:
        result, html = await fetch_text(session, args.url, use_cache=False, force_refresh=True,
                                       max_retries=1, max_body_bytes=2 * 1024 * 1024)
    received = datetime.now(timezone.utc).isoformat()
    sha = hashlib.sha256(result.body).hexdigest()
    args.output.mkdir(parents=True, exist_ok=True)
    raw_path = args.output / (sha + ".raw")
    raw_path.write_bytes(result.body)
    evidence = {"request_url": args.url, "url": result.url, "status": result.status,
                "source": result.source, "request_started_at": started, "received_at": received,
                "body_sha256": sha, "body_bytes": len(result.body), "raw_path": str(raw_path),
                "purpose": "parser technical validation only; not prospective observations"}
    (args.output / (sha + ".receipt.json")).write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False))
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    print(str(soup.title))
    for selector in (".RaceData01", ".RaceData02", ".RaceList_Box", ".RaceList_DataItem", "table.Shutuba_Table tr.HorseList", ".Payback_Table"):
        items = soup.select(selector)
        print(selector, len(items))
        if items:
            print(str(items[0])[:12000])
    if result.body.lstrip().startswith(b"{"):
        print(result.body[:5000].decode("utf-8", errors="replace"))


if __name__ == "__main__":
    asyncio.run(main())
