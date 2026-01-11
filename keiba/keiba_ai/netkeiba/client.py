from __future__ import annotations
import hashlib
import requests
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from ..config import NetkeibaConfig, StorageConfig
from ..utils import sleep_jitter, ensure_parent

class NetkeibaBlockedError(RuntimeError):
    pass

@dataclass
class FetchResult:
    url: str
    status_code: int
    text: str
    from_cache: bool

def _hash_url(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()

class NetkeibaClient:
    def __init__(self, nk: NetkeibaConfig, st: StorageConfig) -> None:
        self.nk = nk
        self.st = st
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": nk.user_agent,
            "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Connection": "keep-alive",
        })
        self._pages_fetched = 0

    def _cache_path(self, kind: str, key: str) -> Path:
        # kind: list/shutuba/result
        return self.st.html_dir / kind / f"{key}.html"

    def fetch_html(self, url: str, cache_kind: str, cache_key: str, use_cache: bool = True) -> FetchResult:
        if self._pages_fetched >= self.nk.max_pages_per_run:
            raise RuntimeError(f"Safety cap reached: max_pages_per_run={self.nk.max_pages_per_run}")

        cache_path = self._cache_path(cache_kind, cache_key)
        if use_cache and self.nk.cache_html and cache_path.exists():
            return FetchResult(url=url, status_code=200, text=cache_path.read_text(encoding='utf-8', errors='replace'), from_cache=True)

        # polite delay
        sleep_jitter(self.nk.min_sleep_sec, self.nk.max_sleep_sec)

        resp = self.session.get(url, timeout=self.nk.timeout_sec)
        self._pages_fetched += 1

        # If netkeiba blocks or rate-limits, STOP rather than trying to bypass.
        if resp.status_code in (401, 403, 429):
            raise NetkeibaBlockedError(f"Blocked or rate-limited (status={resp.status_code}). Stop scraping and reduce frequency.")

        # Handle encodings (netkeiba sometimes serves Shift_JIS/CP932)
        enc = resp.encoding or resp.apparent_encoding or "utf-8"
        try:
            text = resp.content.decode(enc, errors="replace")
        except Exception:
            text = resp.text  # fallback

        if self.nk.cache_html:
            ensure_parent(cache_path)
            cache_path.write_text(text, encoding="utf-8", errors="replace")

        return FetchResult(url=url, status_code=resp.status_code, text=text, from_cache=False)

    def build_url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return f"{self.nk.base}{path}"
    
    def fetch_top_page_race_ids(self, use_cache: bool = True) -> list[str]:
        """
        netkeibaトップページから現在開催中/直近のレースIDを取得
        """
        from .parsers import extract_today_race_ids_from_top
        
        url = f"{self.nk.base}/top/"
        result = self.fetch_html(url, cache_kind="list", cache_key="top_today", use_cache=use_cache)
        race_ids = extract_today_race_ids_from_top(result.text)
        
        return race_ids
    
    def fetch_race_calendar(self, use_cache: bool = True) -> dict[str, list[str]]:
        """
        netkeibaトップページから開催カレンダー情報を取得
        Returns: {kaisai_date: [race_id, ...]}
        """
        from .parsers import extract_race_calendar
        
        url = f"{self.nk.base}/top/"
        result = self.fetch_html(url, cache_kind="list", cache_key="top_calendar", use_cache=use_cache)
        calendar = extract_race_calendar(result.text)
        
        return calendar
    
    def fetch_race_list_by_date(self, kaisai_date: str, use_cache: bool = True) -> list[str]:
        """
        指定日のレース一覧を取得
        Args:
            kaisai_date: YYYYMMDD形式の日付文字列
        Returns:
            race_idのリスト
        """
        from .parsers import extract_race_ids
        
        # race_list_sub.htmlを使う（これが確実）
        url = f"{self.nk.base}/top/race_list_sub.html?kaisai_date={kaisai_date}"
        result = self.fetch_html(url, cache_kind="list", cache_key=f"racelist_sub_{kaisai_date}", use_cache=use_cache)
        race_ids = extract_race_ids(result.text)
        
        return race_ids
