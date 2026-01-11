"""
Playwright/Seleniumを使用したブラウザ経由のスクレイピング
IPブロック回避に有効
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import time
import random

@dataclass
class BrowserFetchResult:
    url: str
    status_code: int
    text: str
    from_cache: bool


class PlaywrightClient:
    """Playwrightを使用したブラウザ自動化クライアント"""
    
    def __init__(self, nk_config, st_config, headless: bool = True):
        self.nk = nk_config
        self.st = st_config
        self.headless = headless
        self._browser = None
        self._context = None
        self._page = None
        self._pages_fetched = 0
        
    def _init_browser(self):
        """ブラウザを初期化（遅延初期化）"""
        if self._browser is not None:
            return
        
        try:
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()
            
            # Chromiumを起動（より人間らしく見える設定）
            self._browser = self._playwright.chromium.launch(
                headless=self.headless,
                args=[
                    '--disable-blink-features=AutomationControlled',  # 自動化検出を無効化
                    '--disable-dev-shm-usage',
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-web-security',
                ]
            )
            
            # コンテキスト作成（実際のブラウザのように見せる）
            self._context = self._browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent=self.nk.user_agent,
                locale='ja-JP',
                timezone_id='Asia/Tokyo',
                extra_http_headers={
                    'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8',
                }
            )
            
            # JavaScriptでwebdriver検出を回避
            self._context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """)
            
            self._page = self._context.new_page()
            print("✅ Playwrightブラウザを起動しました")
            
        except ImportError:
            raise RuntimeError(
                "Playwrightがインストールされていません。\n"
                "インストール方法:\n"
                "  pip install playwright\n"
                "  playwright install chromium"
            )
    
    def fetch_html(self, url: str, cache_kind: str, cache_key: str, use_cache: bool = True) -> BrowserFetchResult:
        """ブラウザを使ってHTMLを取得"""
        
        # キャッシュチェック
        cache_path = self.st.html_dir / cache_kind / f"{cache_key}.html"
        if use_cache and self.nk.cache_html and cache_path.exists():
            return BrowserFetchResult(
                url=url,
                status_code=200,
                text=cache_path.read_text(encoding='utf-8', errors='replace'),
                from_cache=True
            )
        
        # ページ数制限チェック
        if self._pages_fetched >= self.nk.max_pages_per_run:
            raise RuntimeError(f"ページ取得上限に達しました: {self.nk.max_pages_per_run}")
        
        # ブラウザ初期化
        self._init_browser()
        
        # 人間らしい待機時間
        sleep_time = random.uniform(self.nk.min_sleep_sec, self.nk.max_sleep_sec)
        print(f"⏳ {sleep_time:.1f}秒待機中...")
        time.sleep(sleep_time)
        
        try:
            # ページ遷移（タイムアウト付き）
            response = self._page.goto(url, wait_until='domcontentloaded', timeout=60000)
            
            # 少し待つ（JavaScriptの実行を待つ）
            self._page.wait_for_timeout(random.randint(1000, 3000))
            
            # ランダムなスクロール（人間らしい動作）
            if random.random() < 0.3:  # 30%の確率でスクロール
                self._page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
                self._page.wait_for_timeout(random.randint(500, 1500))
            
            # HTMLを取得
            text = self._page.content()
            status_code = response.status if response else 200
            
            self._pages_fetched += 1
            
            # キャッシュに保存
            if self.nk.cache_html:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(text, encoding='utf-8', errors='replace')
            
            print(f"✅ 取得成功: {url} (status={status_code})")
            
            return BrowserFetchResult(
                url=url,
                status_code=status_code,
                text=text,
                from_cache=False
            )
            
        except Exception as e:
            print(f"❌ 取得失敗: {url} - {e}")
            raise
    
    def close(self):
        """ブラウザを終了"""
        if self._page:
            self._page.close()
        if self._context:
            self._context.close()
        if self._browser:
            self._browser.close()
        if hasattr(self, '_playwright'):
            self._playwright.stop()
        print("🔚 Playwrightブラウザを終了しました")
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class SeleniumClient:
    """Seleniumを使用したブラウザ自動化クライアント（代替案）"""
    
    def __init__(self, nk_config, st_config, headless: bool = True):
        self.nk = nk_config
        self.st = st_config
        self.headless = headless
        self._driver = None
        self._pages_fetched = 0
    
    def _init_driver(self):
        """Seleniumドライバーを初期化"""
        if self._driver is not None:
            return
        
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
            
            options = Options()
            if self.headless:
                options.add_argument('--headless')
            
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--no-sandbox')
            options.add_argument(f'user-agent={self.nk.user_agent}')
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option('useAutomationExtension', False)
            
            self._driver = webdriver.Chrome(options=options)
            
            # webdriver検出を回避
            self._driver.execute_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            
            print("✅ Seleniumブラウザを起動しました")
            
        except ImportError:
            raise RuntimeError(
                "Seleniumがインストールされていません。\n"
                "インストール方法:\n"
                "  pip install selenium\n"
                "  # ChromeDriverも必要です"
            )
    
    def fetch_html(self, url: str, cache_kind: str, cache_key: str, use_cache: bool = True) -> BrowserFetchResult:
        """Seleniumでブラウザを使ってHTMLを取得"""
        
        # キャッシュチェック
        cache_path = self.st.html_dir / cache_kind / f"{cache_key}.html"
        if use_cache and self.nk.cache_html and cache_path.exists():
            return BrowserFetchResult(
                url=url,
                status_code=200,
                text=cache_path.read_text(encoding='utf-8', errors='replace'),
                from_cache=True
            )
        
        if self._pages_fetched >= self.nk.max_pages_per_run:
            raise RuntimeError(f"ページ取得上限に達しました: {self.nk.max_pages_per_run}")
        
        self._init_driver()
        
        # 待機
        sleep_time = random.uniform(self.nk.min_sleep_sec, self.nk.max_sleep_sec)
        print(f"⏳ {sleep_time:.1f}秒待機中...")
        time.sleep(sleep_time)
        
        try:
            self._driver.get(url)
            
            # ページ読み込み待機
            time.sleep(random.uniform(2, 4))
            
            # ランダムスクロール
            if random.random() < 0.3:
                self._driver.execute_script("window.scrollTo(0, document.body.scrollHeight / 2)")
                time.sleep(random.uniform(0.5, 1.5))
            
            text = self._driver.page_source
            self._pages_fetched += 1
            
            # キャッシュ保存
            if self.nk.cache_html:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(text, encoding='utf-8', errors='replace')
            
            print(f"✅ 取得成功: {url}")
            
            return BrowserFetchResult(url=url, status_code=200, text=text, from_cache=False)
            
        except Exception as e:
            print(f"❌ 取得失敗: {url} - {e}")
            raise
    
    def close(self):
        """ドライバーを終了"""
        if self._driver:
            self._driver.quit()
        print("🔚 Seleniumブラウザを終了しました")
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
