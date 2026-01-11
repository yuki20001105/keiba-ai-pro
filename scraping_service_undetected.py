"""
undetected-chromedriver + レート制限機能付きスクレイピングサービス
通常IP優先、ブロック時はVPN推奨
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import undetected_chromedriver as uc
from bs4 import BeautifulSoup
import requests
import time
import random
from datetime import datetime
from typing import Optional
import threading

app = FastAPI()

# CORS設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# レート制限管理
class RateLimiter:
    def __init__(self, min_interval=3.0, max_interval=7.0):
        self.min_interval = min_interval
        self.max_interval = max_interval
        self.last_request_time: Optional[datetime] = None
        self.request_count = 0
        self.start_time = datetime.now()
        self.lock = threading.Lock()
    
    def wait_if_needed(self):
        """必要に応じて待機"""
        with self.lock:
            if self.last_request_time is None:
                self.last_request_time = datetime.now()
                return 0
            
            elapsed = (datetime.now() - self.last_request_time).total_seconds()
            required_wait = random.uniform(self.min_interval, self.max_interval)
            
            if elapsed < required_wait:
                wait_time = required_wait - elapsed
                print(f"⏰ レート制限: {wait_time:.1f}秒待機します...")
                time.sleep(wait_time)
            else:
                wait_time = 0
            
            self.last_request_time = datetime.now()
            self.request_count += 1
            
            total_elapsed = (datetime.now() - self.start_time).total_seconds()
            avg_interval = total_elapsed / self.request_count if self.request_count > 0 else 0
            print(f"📊 リクエスト統計: {self.request_count}回, 平均間隔: {avg_interval:.1f}秒")
            
            return wait_time

# グローバルなレート制限インスタンス
rate_limiter = RateLimiter(min_interval=3.0, max_interval=7.0)

# グローバルなChromeドライバー（再利用で高速化）
_driver: Optional[uc.Chrome] = None
_driver_lock = threading.Lock()

def get_driver():
    """Chromeドライバーを取得（シングルトンパターン）"""
    global _driver
    with _driver_lock:
        if _driver is None:
            print("🚀 Chrome WebDriver初期化中...")
            options = uc.ChromeOptions()
            options.headless = False  # 非ヘッドレスモード（安定性優先）
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            _driver = uc.Chrome(options=options, use_subprocess=False, version_main=None)
            print("✓ Chrome WebDriver初期化完了")
        return _driver

class ScrapeRequest(BaseModel):
    race_id: str

class RaceListRequest(BaseModel):
    kaisai_date: str  # YYYYMMDD形式

class RaceListResponse(BaseModel):
    success: bool
    race_ids: list[str] = []
    error: str | None = None

class ScrapeResponse(BaseModel):
    success: bool
    race_name: str | None = None
    race_data: str | None = None
    distance: int | None = None
    track_type: str | None = None
    weather: str | None = None
    field_condition: str | None = None
    results: list[dict] = []
    payouts: list[dict] = []
    error: str | None = None
    wait_time: float | None = None


def check_ip_blocked():
    """現在のIPがブロックされているかチェック（注意：undetected-chromedriverなら回避可能）"""
    try:
        # 通常のrequestsで軽量チェック
        test_response = requests.get(
            'https://race.netkeiba.com/',
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'},
            timeout=10
        )
        
        # 400エラーの場合は確実にブロック
        if test_response.status_code == 400:
            return True
        # 50バイト程度の小さなレスポンスは警告のみ（undetected-chromedriverなら回避可能）
        if len(test_response.content) < 10000:
            print("  ⚠ 通常のrequestsではブロックされていますが、undetected-chromedriverで試行します")
            return False
        return False
        
    except Exception as e:
        # エラーの場合も試行する（undetected-chromedriverで回避できる可能性あり）
        print(f"  ⚠ IP状態チェックエラー（{type(e).__name__}）、undetected-chromedriverで試行します")
        return False

@app.post("/scrape/race", response_model=ScrapeResponse)
def scrape_race(request: ScrapeRequest):
    """
    レース結果をスクレイピング（undetected-chromedriver使用）
    - 初回リクエスト時にIP状態をチェック（400エラーの場合のみVPN推奨）
    - それ以外はundetected-chromedriverで取得を試行
    """
    # レート制限チェック
    wait_time = rate_limiter.wait_if_needed()
    
    race_id = request.race_id
    url = f'https://race.netkeiba.com/race/result.html?race_id={race_id}'
    
    # IP状態チェック（初回リクエスト時のみ、400エラーの場合のみブロック判定）
    if rate_limiter.request_count == 1:
        print("→ IP状態チェック中...")
        if check_ip_blocked():
            print("✗ 確実にブロックされています（400エラー）")
            return ScrapeResponse(
                success=False,
                error="IPアドレスが完全にブロックされています（400エラー）。ProtonVPN等のVPNに接続してから再度お試しください。",
                wait_time=wait_time
            )
        else:
            print("✓ 通常IPでアクセス可能")
    
    try:
        # Chromeドライバーを取得
        driver = get_driver()
        
        print(f"→ Opening URL: {url}")
        
        # 人間らしい遅延
        time.sleep(random.uniform(1.5, 3.0))
        
        # ページを開く
        driver.get(url)
        
        # JavaScriptレンダリング待機
        time.sleep(random.uniform(2.0, 4.0))
        
        # ページタイトルを確認
        title = driver.title
        print(f"✓ Page loaded: {title}")
        
        # HTMLを取得
        html = driver.page_source
        content_length = len(html)
        print(f"✓ HTML retrieved: {content_length:,} bytes")
        
        # エラーページのチェック
        if content_length < 10000:
            return ScrapeResponse(
                success=False,
                error="ページが正常に読み込まれませんでした。race_idを確認してください。",
                wait_time=wait_time
            )
        
        # BeautifulSoupでパース
        soup = BeautifulSoup(html, 'html.parser')
        
        # レース名
        race_name_elem = soup.find('h1', class_='RaceName')
        if not race_name_elem:
            # 出馬表ページの可能性もチェック
            race_name_elem = soup.find('div', class_='RaceName')
        
        if not race_name_elem:
            return ScrapeResponse(
                success=False,
                error='レース名が取得できませんでした。race_idが正しいか確認してください。',
                wait_time=wait_time
            )
        
        race_name = race_name_elem.text.strip()
        print(f"✓ レース名: {race_name}")
        
        # レースデータ
        race_data_elem = soup.find('div', class_='RaceData01')
        if not race_data_elem:
            race_data_elem = soup.find('div', class_='RaceData02')
        
        race_data_text = race_data_elem.text.strip() if race_data_elem else ''
        
        # 距離・トラック種別などを抽出
        distance = None
        track_type = ''
        weather = ''
        field_condition = ''
        
        if race_data_text:
            import re
            # 距離
            dist_match = re.search(r'(\d+)m', race_data_text)
            if dist_match:
                distance = int(dist_match.group(1))
            
            # トラック種別
            if '芝' in race_data_text:
                track_type = '芝'
            elif 'ダート' in race_data_text or 'ダ' in race_data_text:
                track_type = 'ダート'
            
            # 天候
            weather_match = re.search(r'天候:([^/\s]+)', race_data_text)
            if weather_match:
                weather = weather_match.group(1).strip()
            
            # 馬場状態
            field_match = re.search(r'馬場:([^/\s]+)', race_data_text)
            if field_match:
                field_condition = field_match.group(1).strip()
        
        # 結果テーブル
        results = []
        result_table = soup.find('table', class_='Result_Table')
        if result_table:
            rows = result_table.find_all('tr')
            for row in rows[1:]:
                cols = row.find_all('td')
                if len(cols) >= 10:
                    result = {
                        'finish_position': cols[0].text.strip(),
                        'bracket_number': cols[1].text.strip(),
                        'horse_number': cols[2].text.strip(),
                        'horse_name': cols[3].text.strip(),
                        'sex_age': cols[4].text.strip(),
                        'jockey_weight': cols[5].text.strip(),
                        'jockey_name': cols[6].text.strip(),
                        'finish_time': cols[7].text.strip(),
                        'margin': cols[8].text.strip(),
                        'odds': cols[9].text.strip(),
                    }
                    results.append(result)
        
        print(f"✓ 結果: {len(results)}頭分のデータ")
        
        # 払い戻しテーブル
        payouts = []
        payout_table = soup.find('table', class_='Payout_Detail_Table')
        if payout_table:
            rows = payout_table.find_all('tr')
            for row in rows:
                cols = row.find_all('td')
                if len(cols) >= 3:
                    payout = {
                        'type': cols[0].text.strip(),
                        'numbers': cols[1].text.strip(),
                        'amount': cols[2].text.strip(),
                    }
                    payouts.append(payout)
        
        print(f"✓ 払い戻し: {len(payouts)}件")
        
        return ScrapeResponse(
            success=True,
            race_name=race_name,
            race_data=race_data_text,
            distance=distance,
            track_type=track_type,
            weather=weather,
            field_condition=field_condition,
            results=results,
            payouts=payouts,
            wait_time=wait_time
        )
        
    except Exception as e:
        print(f"✗ エラー: {type(e).__name__}: {str(e)}")
        return ScrapeResponse(success=False, error=str(e), wait_time=wait_time)


@app.get("/health")
def health_check():
    """ヘルスチェック"""
    return {
        "status": "ok",
        "request_count": rate_limiter.request_count,
        "uptime_seconds": (datetime.now() - rate_limiter.start_time).total_seconds(),
        "driver_initialized": _driver is not None
    }


@app.get("/stats")
def get_stats():
    """統計情報"""
    total_elapsed = (datetime.now() - rate_limiter.start_time).total_seconds()
    avg_interval = total_elapsed / rate_limiter.request_count if rate_limiter.request_count > 0 else 0
    
    return {
        "total_requests": rate_limiter.request_count,
        "uptime_seconds": total_elapsed,
        "average_interval_seconds": avg_interval,
        "rate_limit_config": {
            "min_interval": rate_limiter.min_interval,
            "max_interval": rate_limiter.max_interval
        },
        "driver_status": "initialized" if _driver is not None else "not initialized"
    }


@app.post("/race_list", response_model=RaceListResponse)
def get_race_list(request: RaceListRequest):
    """
    指定日のrace_id一覧を取得
    race_list.html?kaisai_date=YYYYMMDDから実際のrace_idを取得
    """
    kaisai_date = request.kaisai_date
    url = f'https://race.netkeiba.com/top/race_list.html?kaisai_date={kaisai_date}'
    
    print(f"📅 {kaisai_date[:4]}年{kaisai_date[4:6]}月{kaisai_date[6:8]}日のレース一覧取得中...")
    
    try:
        driver = get_driver()
        driver.get(url)
        
        # ページ読み込み待機
        time.sleep(random.uniform(2.0, 3.0))
        
        html = driver.page_source
        soup = BeautifulSoup(html, 'html.parser')
        
        # race_idを抽出
        import re
        race_ids = []
        for link in soup.find_all('a', href=True):
            href = link['href']
            match = re.search(r'race_id=(\d{12})', href)
            if match:
                race_id = match.group(1)
                if race_id not in race_ids:
                    race_ids.append(race_id)
        
        print(f"✓ {len(race_ids)}件のレースを取得")
        
        return RaceListResponse(
            success=True,
            race_ids=race_ids
        )
        
    except Exception as e:
        print(f"✗ エラー: {e}")
        return RaceListResponse(
            success=False,
            error=str(e)
        )


@app.on_event("shutdown")
def shutdown_event():
    """シャットダウン時にドライバーをクリーンアップ"""
    global _driver
    if _driver is not None:
        print("🛑 Chrome WebDriverをクローズします...")
        _driver.quit()
        _driver = None


if __name__ == '__main__':
    import uvicorn
    print("=" * 80)
    print("undetected-chromedriver + レート制限機能付きスクレイピングサービス起動")
    print("=" * 80)
    print(f"最小間隔: {rate_limiter.min_interval}秒")
    print(f"最大間隔: {rate_limiter.max_interval}秒")
    print(f"Bot回避: undetected-chromedriver使用")
    print("=" * 80)
    uvicorn.run(app, host='0.0.0.0', port=8001)
