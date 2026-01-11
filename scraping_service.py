"""
FastAPI scraping service using Selenium
Next.jsからHTTP経由で呼び出される
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from selenium import webdriver
from selenium.webdriver.edge.service import Service
from selenium.webdriver.edge.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.microsoft import EdgeChromiumDriverManager
from bs4 import BeautifulSoup
import time
import random

app = FastAPI()

# CORS設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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


@app.post("/scrape/race", response_model=ScrapeResponse)
def scrape_race(request: ScrapeRequest):
    """
    レース結果をスクレイピング
    """
    race_id = request.race_id
    url = f'https://race.netkeiba.com/race/result.html?race_id={race_id}'
    
    try:
        # Edgeオプション設定（bot検出回避）
        edge_options = Options()
        # edge_options.add_argument('--headless')  # デバッグのため一旦無効化
        edge_options.add_argument('--no-sandbox')
        edge_options.add_argument('--disable-dev-shm-usage')
        edge_options.add_argument('--disable-blink-features=AutomationControlled')
        edge_options.add_experimental_option('excludeSwitches', ['enable-automation'])
        edge_options.add_experimental_option('useAutomationExtension', False)
        edge_options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0')
        
        # WebDriverを初期化
        service = Service(EdgeChromiumDriverManager().install())
        driver = webdriver.Edge(service=service, options=edge_options)
        
        print(f"✓ Edge WebDriver initialized")
        
        # webdriver検出を回避
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        print(f"✓ Anti-bot script injected")
        
        try:
            # 人間らしい遅延
            time.sleep(random.uniform(1.0, 2.5))
            
            # ページを開く
            print(f"→ Opening URL: {url}")
            driver.get(url)
            print(f"✓ Page loaded: {driver.title}")
            
            # ページのURLを確認（リダイレクトされていないか）
            current_url = driver.current_url
            print(f"  Current URL: {current_url}")
            
            # レース名が表示されるまで待機（最大15秒）
            try:
                print(f"→ Waiting for h1.RaceName element...")
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, 'h1.RaceName'))
                )
                print(f"✓ h1.RaceName found")
            except Exception as e:
                print(f"✗ h1.RaceName not found: {e}")
                # ページのHTMLを確認
                print(f"  Page source length: {len(driver.page_source)}")
                print(f"  First 500 chars: {driver.page_source[:500]}")
            
            # 追加の待機（JavaScriptの完全実行を確保）
            time.sleep(random.uniform(2.0, 3.5))
            
            # HTMLを取得
            html = driver.page_source
            print(f"✓ HTML retrieved: {len(html)} bytes")
            
        finally:
            # ブラウザを閉じる
            driver.quit()
            print(f"✓ Browser closed")
        
        # BeautifulSoupでパース
        soup = BeautifulSoup(html, 'html.parser')
        
        # レース名
        race_name_elem = soup.find('h1', class_='RaceName')
        if not race_name_elem:
            return ScrapeResponse(success=False, error='レース名が取得できませんでした')
        
        race_name = race_name_elem.text.strip()
        
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
        
        return ScrapeResponse(
            success=True,
            race_name=race_name,
            race_data=race_data_text,
            distance=distance,
            track_type=track_type,
            weather=weather,
            field_condition=field_condition,
            results=results,
            payouts=payouts
        )
        
    except Exception as e:
        return ScrapeResponse(success=False, error=str(e))


@app.get("/health")
def health_check():
    """ヘルスチェック"""
    return {"status": "ok"}


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8001)
