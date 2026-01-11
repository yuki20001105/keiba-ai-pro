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
    cfg = load_config(cfg_path)
    con = connect(cfg.storage.sqlite_path)
    init_db(con)

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
        kaisai_date = None
        if re.match(r"^\d{12}$", race_id):
            kaisai_date = race_id[:8]

        upsert_race(con, race_id=race_id, kaisai_date=kaisai_date, source="netkeiba")

        if fetch_shutuba:
            url = cfg.netkeiba.base + cfg.netkeiba.shutuba_url.format(race_id=race_id)
            try:
                fr = client.fetch_html(url, cache_kind="shutuba", cache_key=race_id, use_cache=True)
            except NetkeibaBlockedError as e:
                if not use_browser:
                    print(f"⚠️ IPブロック検出: {e}")
                    print("🔄 ブラウザモードで自動再試行中...")
                    if hasattr(client, 'close'):
                        client.close()
                    try:
                        from .netkeiba.browser_client import PlaywrightClient
                        client = PlaywrightClient(cfg.netkeiba, cfg.storage, headless=True)
                        use_browser = True
                        fr = client.fetch_html(url, cache_kind="shutuba", cache_key=race_id, use_cache=True)
                    except ImportError:
                        print("❌ Playwrightが未インストールのため自動リトライ不可")
                        print("💡 インストール: playwright install chromium")
                        raise
                else:
                    raise
            df = parse_shutuba_table(fr.text)
            upsert_entries(con, race_id, df)

        if fetch_result:
            url = cfg.netkeiba.base + cfg.netkeiba.result_url.format(race_id=race_id)
            try:
                fr = client.fetch_html(url, cache_kind="result", cache_key=race_id, use_cache=True)
            except NetkeibaBlockedError as e:
                if not use_browser:
                    print(f"⚠️ IPブロック検出: {e}")
                    print("🔄 ブラウザモードで自動再試行中...")
                    if hasattr(client, 'close'):
                        client.close()
                    try:
                        from .netkeiba.browser_client import PlaywrightClient
                        client = PlaywrightClient(cfg.netkeiba, cfg.storage, headless=True)
                        use_browser = True
                        fr = client.fetch_html(url, cache_kind="result", cache_key=race_id, use_cache=True)
                    except ImportError:
                        print("❌ Playwrightが未インストールのため自動リトライ不可")
                        print("💡 インストール: playwright install chromium")
                        raise
                else:
                    raise
            df = parse_result_table(fr.text)
            upsert_results(con, race_id, df)

    finally:
        if use_browser and hasattr(client, 'close'):
            client.close()
        con.close()


def ingest_by_date(config_path: Path, yyyymmdd: str, use_browser: bool = False) -> list[str]:
    cfg = load_config(config_path)

    if use_browser:
        try:
            from .netkeiba.browser_client import PlaywrightClient
            client = PlaywrightClient(cfg.netkeiba, cfg.storage, headless=True)
        except ImportError:
            client = NetkeibaClient(cfg.netkeiba, cfg.storage)
            use_browser = False
    else:
        client = NetkeibaClient(cfg.netkeiba, cfg.storage)

    try:
        list_url = cfg.netkeiba.base + f"/top/race_list.html?kaisai_date={yyyymmdd}"
        try:
            fr = client.fetch_html(list_url, cache_kind="list", cache_key=yyyymmdd, use_cache=True)
        except NetkeibaBlockedError as e:
            if not use_browser:
                print(f"⚠️ IPブロック検出: {e}")
                print("🔄 ブラウザモードで自動再試行中...")
                if hasattr(client, 'close'):
                    client.close()
                try:
                    from .netkeiba.browser_client import PlaywrightClient
                    client = PlaywrightClient(cfg.netkeiba, cfg.storage, headless=True)
                    use_browser = True
                    fr = client.fetch_html(list_url, cache_kind="list", cache_key=yyyymmdd, use_cache=True)
                except ImportError:
                    print("❌ Playwrightが未インストールのため自動リトライ不可")
                    print("💡 インストール: playwright install chromium")
                    raise
            else:
                raise
        
        race_ids = extract_race_ids(fr.text)
        if race_ids:
            return race_ids

        group_ids = sorted(set(re.findall(r"current_group=(\d+)", fr.text)))
        all_ids: set[str] = set()
        for gid in group_ids:
            sub_url = cfg.netkeiba.base + f"/top/race_list_sub.html?current_group={gid}&kaisai_date={yyyymmdd}"
            try:
                fr2 = client.fetch_html(sub_url, cache_kind="list_sub", cache_key=f"{yyyymmdd}_{gid}", use_cache=True)
            except NetkeibaBlockedError as e:
                if not use_browser:
                    print(f"⚠️ IPブロック検出: {e}")
                    print("🔄 ブラウザモードで自動再試行中...")
                    if hasattr(client, 'close'):
                        client.close()
                    try:
                        from .netkeiba.browser_client import PlaywrightClient
                        client = PlaywrightClient(cfg.netkeiba, cfg.storage, headless=True)
                        use_browser = True
                        fr2 = client.fetch_html(sub_url, cache_kind="list_sub", cache_key=f"{yyyymmdd}_{gid}", use_cache=True)
                    except ImportError:
                        print("❌ Playwrightが未インストールのため自動リトライ不可")
                        print("💡 インストール: playwright install chromium")
                        raise
                else:
                    raise
            for rid in extract_race_ids(fr2.text):
                all_ids.add(rid)
        return sorted(all_ids)
    finally:
        if use_browser and hasattr(client, 'close'):
            client.close()


def main(argv: Optional[Sequence[str]] = None) -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--browser", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    p_one = sub.add_parser("race")
    p_one.add_argument("race_id")
    p_one.add_argument("--no_shutuba", action="store_true")
    p_one.add_argument("--no_result", action="store_true")
    p_date = sub.add_parser("date")
    p_date.add_argument("date")
    args = p.parse_args(argv)
    use_browser = args.browser if hasattr(args, 'browser') else False

    if args.cmd == "date":
        ids = ingest_by_date(Path(args.config), args.date, use_browser=use_browser)
        for x in ids:
            print(x)
        return

    if args.cmd == "race":
        ingest_one_race(Path(args.config), args.race_id, fetch_shutuba=not args.no_shutuba, fetch_result=not args.no_result, use_browser=use_browser)
        print("OK")


if __name__ == "__main__":
    main()
