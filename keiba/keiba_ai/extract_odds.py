"""
JRA公式サイトからリアルタイムオッズを取得するモジュール
"""
from __future__ import annotations
import asyncio
import re
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

import pandas as pd
from playwright.async_api import async_playwright, Page


@dataclass
class RealtimeOdds:
    """JRA公式サイトからオッズを取得するクラス"""
    
    race_id: str
    htmls: dict[str, str] = field(default_factory=dict)
    tansho: dict[int, float] = field(default_factory=dict)
    tanpuku: dict[int, tuple[float, float]] = field(default_factory=dict)
    umatan: dict[str, float] = field(default_factory=dict)
    umaren: dict[str, float] = field(default_factory=dict)
    wakuren: dict[str, tuple[float, float]] = field(default_factory=dict)
    sanrentan: dict[str, float] = field(default_factory=dict)
    sanrenpuku: dict[str, float] = field(default_factory=dict)

    async def scrape_html(
        self,
        skip_bet_types: list[str] = ["wakuren", "wide"],
        headless: bool = True,
        delay_time: int = 1000,
    ) -> None:
        """
        レースIDを指定してJRA公式サイトからオッズページのHTMLを取得する関数。

        Parameters
        --------
        skip_bet_types : list[str], optional
            スキップする馬券種のリスト。デフォルトは["wakuren", "wide"]
        headless : bool, optional
            ブラウザをヘッドレスモードで実行するかどうか。デフォルトはTrue
        delay_time : int, optional
            ページ遷移時の遅延時間（ミリ秒）。デフォルトは1000

        Returns
        --------
        None
            結果はインスタンス変数 self.htmls に辞書形式で格納される。
        """
        try:
            import asyncio
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        bet_types = ["tanpuku", "umatan", "umaren", "sanrenpuku", "sanrentan"]
        bet_types = [b for b in bet_types if b not in skip_bet_types]

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=headless)
                page = await browser.new_page()

                base_url = f"https://www.jra.go.jp/keiba/repogai/oddshtml/{self.race_id}/"

                for bet_type in bet_types:
                    try:
                        url = f"{base_url}o{self.race_id[2:]}{self._get_bet_type_code(bet_type)}.html"
                        await page.goto(url, wait_until="networkidle")
                        await asyncio.sleep(delay_time / 1000)
                        content = await page.content()
                        self.htmls[bet_type] = content
                    except Exception as e:
                        print(f"Failed to scrape {bet_type}: {e}")

                await browser.close()
        except Exception as e:
            print(f"Playwright error: {e}")

    def _get_bet_type_code(self, bet_type: str) -> str:
        """馬券種を3文字コードに変換"""
        codes = {
            "tanpuku": "110",
            "umatan": "310",
            "umaren": "220",
            "sanrenpuku": "330",
            "sanrentan": "340",
        }
        return codes.get(bet_type, "")

    def extract_tansho(self) -> None:
        """単勝オッズを抽出"""
        # 実装注：tanpukuのHTMLから単勝オッズを抽出
        if "tanpuku" not in self.htmls:
            return

        html = self.htmls["tanpuku"]
        pattern = r'<td[^>]*>(\d+)</td>\s*<td[^>]*class="oddsvalue"[^>]*>([\d.]+)'
        matches = re.findall(pattern, html, re.IGNORECASE)

        self.tansho = {int(uma): float(odds) for uma, odds in matches}

    def extract_tanpuku(self) -> None:
        """複勝オッズを抽出（上限オッズ・下限オッズの扱い）"""
        if "tanpuku" not in self.htmls:
            return

        html = self.htmls["tanpuku"]
        # 複勝は上限オッズ・下限オッズの扱いが複雑なため、ここでは省略
        # 詳細な実装は記事を参照
        pass

    def extract_umatan(self) -> None:
        """馬単オッズを抽出"""
        if "umatan" not in self.htmls:
            return

        html = self.htmls["umatan"]
        pattern = r'<td[^>]*>(\d+),(\d+)</td>\s*<td[^>]*class="oddsvalue"[^>]*>([\d.]+)'
        matches = re.findall(pattern, html, re.IGNORECASE)

        self.umatan = {f"{int(m[0]):02d},{int(m[1]):02d}": float(m[2]) for m in matches}

    def extract_umaren(self) -> None:
        """馬連オッズを抽出"""
        if "umaren" not in self.htmls:
            return

        html = self.htmls["umaren"]
        pattern = r'<td[^>]*>(\d+),(\d+)</td>\s*<td[^>]*class="oddsvalue"[^>]*>([\d.]+)'
        matches = re.findall(pattern, html, re.IGNORECASE)

        # 馬連は順序を考慮しない
        odds_dict = {}
        for m in matches:
            uma1, uma2 = int(m[0]), int(m[1])
            key = f"{min(uma1, uma2):02d},{max(uma1, uma2):02d}"
            odds_dict[key] = float(m[2])

        self.umaren = odds_dict

    def extract_sanrenpuku(self) -> None:
        """三連複オッズを抽出"""
        if "sanrenpuku" not in self.htmls:
            return

        html = self.htmls["sanrenpuku"]
        pattern = r'<td[^>]*>(\d+),(\d+),(\d+)</td>\s*<td[^>]*class="oddsvalue"[^>]*>([\d.]+)'
        matches = re.findall(pattern, html, re.IGNORECASE)

        # 三連複は順序を考慮しない
        odds_dict = {}
        for m in matches:
            umas = sorted([int(m[0]), int(m[1]), int(m[2])])
            key = f"{umas[0]:02d},{umas[1]:02d},{umas[2]:02d}"
            odds_dict[key] = float(m[3])

        self.sanrenpuku = odds_dict

    def extract_sanrentan(self) -> None:
        """三連単オッズを抽出"""
        if "sanrentan" not in self.htmls:
            return

        html = self.htmls["sanrentan"]
        pattern = r'<td[^>]*>(\d+),(\d+),(\d+)</td>\s*<td[^>]*class="oddsvalue"[^>]*>([\d.]+)'
        matches = re.findall(pattern, html, re.IGNORECASE)

        self.sanrentan = {f"{int(m[0]):02d},{int(m[1]):02d},{int(m[2]):02d}": float(m[3]) for m in matches}

    def extract_all(self) -> None:
        """全ての馬券種のオッズを抽出"""
        self.extract_tansho()
        self.extract_tanpuku()
        self.extract_umatan()
        self.extract_umaren()
        self.extract_sanrenpuku()
        self.extract_sanrentan()
