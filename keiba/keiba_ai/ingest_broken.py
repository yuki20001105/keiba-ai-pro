from __future__ import annotations
import argparse
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence
import re

from .config import load_config
from .db import connect, init_db, upsert_race, upsert_entries, upsert_results
from .netkeiba.client import NetkeibaClient, NetkeibaBlockedError
from .netkeiba.parsers import extract_race_ids, parse_shutuba_table, parse_result_table


def ingest_one_race(cfg_path: Path, race_id: str, fetch_shutuba: bool = True, fetch_result: bool = True, use_browser: bool = False) -> None:
    """レースデータを取得してDBに保存
    
    Args:
        cfg_path: 設定ファイルパス
        race_id: レースID
        fetch_shutuba: 出馬表を取得するか
        fetch_result: 結果を取得するか
        use_browser: Playwrightブラウザモードを使用するか
    """
    cfg = load_config(cfg_path)
    con = connect(cfg.storage.sqlite_path)
    init_db(con)

    # ブラウザモードまたは通常モードを選択
    if use_browser:
        try:
            from .netkeiba.browser_client import PlaywrightClient
            client = PlaywrightClient(cfg.netkeiba, cfg.storage, headless=True)
            print("🌐 Playwrightブラウザモードを使用")
        except ImportError:
            print("⚠️ Playwrightが未インストール。通常モードにフォールバック")
            print("   インストール: pip install playwright && playwright install chromium")
            client = NetkeibaClient(cfg.netkeiba, cfg.storage)
            use_browser = False
    else:
        client = NetkeibaClient(cfg.netkeiba, cfg.storage)

    try:
        kaisai_date = None, use_browser: bool = False) -> list[str]:
    """指定日付のレースIDを取得
    
    Args:
        config_path: 設定ファイルパス
        yyyymmdd: 日付（YYYYMMDD形式）
        use_browser: Playwrightブラウザモードを使用するか
    """
    cfg = load_config(config_path)

    # ブラウザモードまたは通常モードを選択
    if use_browser:
        try:
            from .netkeiba.browser_client import PlaywrightClient
            client = PlaywrightClient(cfg.netkeiba, cfg.storage, headless=True)
            print("🌐 Playwrightブラウザモードを使用")
        except ImportError:
            print("⚠️ Playwrightが未インストール。通常モードにフォールバック")
            client = NetkeibaClient(cfg.netkeiba, cfg.storage)
            use_browser = False
    else:
        client = NetkeibaClient(cfg.netkeiba, cfg.storage)

    try:
        # まずメインの一覧ページを取得
        list_url = cfg.netkeiba.base + f"/top/race_list.html?kaisai_date={yyyymmdd}"
        fr = client.fetch_html(list_url, cache_kind="list", cache_key=yyyymmdd, use_cache=True)

        # ここで取れる場合もある
        race_ids = extract_race_ids(fr.text)
        if race_ids:
            return race_ids

        # ★ fallback：current_group を拾って race_list_sub を取りに行く
        group_ids = sorted(set(re.findall(r"current_group=(\d+)", fr.text)))
        all_ids: set[str] = set()

        for gid in group_ids:
            sub_url = cfg.netkeiba.base + f"/top/race_list_sub.html?current_group={gid}&kaisai_date={yyyymmdd}"
            fr2 = client.fetch_html(
                sub_url,
                cache_kind="list_sub",
                cache_key=f"{yyyymmdd}_{gid}",
                use_cache=True,
            )
            for rid in extract_race_ids(fr2.text):
                all_ids.add(rid)

        return sorted(all_ids)
    
    finally:
        if use_browser and hasattr(client, 'close'):
            client.close(
    # まずメインの一覧ページを取得
    list_url = client.build_url(f"/top/race_list.html?kaisai_date={yyyymmdd}")
    fr = client.fetch_html(list_url, cache_kind="list", cache_key=yyyymmdd, use_cache=True)

    # ここで取れる場合もある
    race_ids = extract_race_ids(fr.text)
    if race_ids:
        return race_ids

    # ★ fallback：current_group を拾って race_list_sub を取りに行く
    group_ids = sorted(set(re.findall(r"current_group=(\d+)", fr.text)))
    all_ids: set[str] = set()

    for gid in group_ids:
        sub_url = client.build_url(f"/top/race_list_sub.html?current_group={gid}&kaisai_date={yyyymmdd}")
        fr2 = client.fetch_html(
            sub_url,
            cache_kind="list_sub",
            cache_key=f"{yyyymmdd}_{gid}",
            use_cache=True,
        )
        for rid in extract_race_ids(fr2.text):
            all_ids.add(rid)

    return sorted(all_ids)

def main(argv: Optional[Sequence[str]] = None) -> None:
    p = argparse.ArgumentParser(description="Ingest netkeiba race data into SQLite (polite, cached).")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--browser", action="store_true", help="Use Playwright browser mode (avoid IP blocking)")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_one = sub.add_parser("race", help="Ingest one race_id (shutuba/result).")
    p_one.add_argument("race_id")
    p_one.add_argument("--no_shutuba", action="store_true")
    p_one.add_argument("--no_result", action="store_true")

    p_date = sub.add_parser("date", help="Fetch race_id list for a date (YYYYMMDD).")
    p_date.add_argument("date")

    args = p.parse_args(argv)
    use_browser = args.browser if hasattr(args, 'browser') else False

    if args.cmd == "date":
        ids = ingest_by_date(Path(args.config), args.date, use_browser=use_browser)
        for x in ids:
            print(x)
        return

    if args.cmd == "race":
        ingest_one_race(
            Path(args.config),
            args.race_id,
            fetch_shutuba=not args.no_shutuba,
            fetch_result=not args.no_result,
            use_browser=use_browser,
        )
        print("OK")
        return

if __name__ == "__main__":
    main()
