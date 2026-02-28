"""
スクレイピング共通定数
"""
from __future__ import annotations

import re

from bs4 import SoupStrainer

# ── HTMLパース最適化: 不要タグを除外してメモリ削減（60〜70%削減） ──────
HTML_STRAINER = SoupStrainer(
    [
        "html", "body",
        "table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption", "col", "colgroup",
        "div", "span", "p", "a", "b", "i", "em", "strong", "small", "br", "hr",
        "h1", "h2", "h3", "h4", "h5", "h6",
        "dl", "dt", "dd", "ul", "ol", "li",
        "form", "input", "select", "option", "img",
        "section", "article", "aside", "header", "footer", "main", "nav",
    ]
)

# ── 競馬場コードマップ（JRA + NAR） ─────────────────────────────────
VENUE_MAP: dict[str, str] = {
    # JRA
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟",
    "05": "東京", "06": "中山", "07": "中京", "08": "京都",
    "09": "阪神", "10": "小倉",
    # NAR
    "30": "門別", "31": "帯広（ば）",
    "35": "盛岡", "36": "水沢",
    "42": "浦和", "43": "船橋", "44": "大井", "45": "川崎",
    "46": "金沢", "47": "笠松", "48": "名古屋",
    "50": "園田", "51": "姫路",
    "54": "福山", "55": "高知",
    "60": "佐賀",
    "65": "帯広(ばんえい)", "66": "中津",
}

# ── HTTP リクエストヘッダー ────────────────────────────────────────
SCRAPE_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Referer": "https://db.netkeiba.com/",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

# ── 毛色定数（長いパターンを先に: 部分マッチ防止） ────────────────────
COAT_COLORS: list[str] = [
    "黒鹿毛", "青鹿駁毛", "青鹿毛", "青駁毛", "鹿駁毛", "栗駁毛", "駁栗毛",
    "駁鹿毛", "駁青毛", "駁青鹿毛", "駁青暴毛", "栃栗毛",
    "鹿毛", "青毛", "栗毛", "芦毛", "白毛", "駁毛",
]
COAT_RE = re.compile("|".join(re.escape(c) for c in COAT_COLORS))
