"""
レート制限機能付きスクレイピングサービス
各リクエスト間に3〜7秒の間隔を確保してIPブロックを防ぐ
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import time
import random
from datetime import datetime, timedelta
from typing import Optional

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
        self.min_interval = min_interval  # 最小間隔（秒）
        self.max_interval = max_interval  # 最大間隔（秒）
        self.last_request_time: Optional[datetime] = None
        self.request_count = 0
        self.start_time = datetime.now()
    
    def wait_if_needed(self):
        """必要に応じて待機"""
        if self.last_request_time is None:
            self.last_request_time = datetime.now()
            return
        
        # 前回のリクエストからの経過時間
        elapsed = (datetime.now() - self.last_request_time).total_seconds()
        
        # ランダムな待機時間（3〜7秒）
        required_wait = random.uniform(self.min_interval, self.max_interval)
        
        if elapsed < required_wait:
            wait_time = required_wait - elapsed
            print(f"⏰ レート制限: {wait_time:.1f}秒待機します...")
            time.sleep(wait_time)
        
        self.last_request_time = datetime.now()
        self.request_count += 1
        
        # 統計情報を表示
        total_elapsed = (datetime.now() - self.start_time).total_seconds()
        avg_interval = total_elapsed / self.request_count if self.request_count > 0 else 0
        print(f"📊 リクエスト統計: {self.request_count}回, 平均間隔: {avg_interval:.1f}秒")

# グローバルなレート制限インスタンス
rate_limiter = RateLimiter(min_interval=3.0, max_interval=7.0)

class ScrapeRequest(BaseModel):
    race_id: str

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
    wait_time: float | None = None  # 待機時間（秒）


@app.post("/scrape/race", response_model=ScrapeResponse)
def scrape_race(request: ScrapeRequest):
    """
    レース結果をスクレイピング（レート制限付き）
    """
    # レート制限チェック
    start_time = time.time()
    rate_limiter.wait_if_needed()
    wait_time = time.time() - start_time
    
    race_id = request.race_id
    url = f'https://race.netkeiba.com/race/result.html?race_id={race_id}'
    
    try:
        # Chromeオプション設定（bot検出回避）
        chrome_options = Options()
        chrome_options.add_argument('--headless=new')  # 新しいヘッドレスモード
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_experimental_option('excludeSwitches', ['enable-automation'])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        chrome_options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        
        # WebDriverを初期化
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        
        print(f"✓ Chrome WebDriver initialized")
        
        # webdriver検出を回避
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        try:
            # 人間らしい遅延
            time.sleep(random.uniform(1.5, 3.0))
            
            # ページを開く
            print(f"→ Opening URL: {url}")
            driver.get(url)
            print(f"✓ Page loaded: {driver.title}")
            
            # ページのURLを確認
            current_url = driver.current_url
            print(f"  Current URL: {current_url}")
            
            # ステータスコードのチェック（JavaScriptで確認）
            status_code = driver.execute_script("return document.readyState")
            print(f"  Page state: {status_code}")
            
            # レース名が表示されるまで待機（最大15秒）
            try:
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, 'h1.RaceName'))
                )
                print(f"✓ レース名要素を検出")
            except Exception as e:
                print(f"⚠ レース名要素が見つかりません: {e}")
                # ページソースを確認
                page_length = len(driver.page_source)
                print(f"  ページサイズ: {page_length} bytes")
                
                if page_length < 1000:
                    # 400エラーまたはブロックの可能性
                    driver.quit()
                    return ScrapeResponse(
                        success=False,
                        error="ページが正常に読み込まれませんでした。IPブロックの可能性があります。",
                        wait_time=wait_time
                    )
            
            # 追加の待機（JavaScriptの完全実行を確保）
            time.sleep(random.uniform(2.0, 4.0))
            
            # HTMLを取得
            html = driver.page_source
            print(f"✓ HTML retrieved: {len(html):,} bytes")
            
        finally:
            # ブラウザを閉じる
            driver.quit()
            print(f"✓ Browser closed")
        
        # BeautifulSoupでパース
        soup = BeautifulSoup(html, 'html.parser')
        
        # レース名
        race_name_elem = soup.find('h1', class_='RaceName')
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
        race_data_text = race_data_elem.text.strip() if race_data_elem else ''
        
        # 距離・トラック種別などを抽出
        distance = None
        track_type = ''
        weather = ''
        field_condition = ''
        
        if race_data_text:
            import re
            # 距離（例: 芝1600m）
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
            for row in rows[1:]:  # ヘッダー行をスキップ
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
        "uptime_seconds": (datetime.now() - rate_limiter.start_time).total_seconds()
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
        }
    }


if __name__ == '__main__':
    import uvicorn
    print("=" * 80)
    print("レート制限機能付きスクレイピングサービス起動")
    print("=" * 80)
    print(f"最小間隔: {rate_limiter.min_interval}秒")
    print(f"最大間隔: {rate_limiter.max_interval}秒")
    print("=" * 80)
    uvicorn.run(app, host='0.0.0.0', port=8001)
