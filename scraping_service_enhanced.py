"""
全特徴量取得対応 - 拡張版スクレイピングサービス
- 馬詳細（血統、過去成績）
- 騎手詳細（勝率、連対率、複勝率）
- 調教師詳細（勝率等）
- ラップタイム、コーナー通過順位
- 結果テーブル全15列
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
import re

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
            
            return wait_time

# グローバルなレート制限インスタンス
rate_limiter = RateLimiter(min_interval=3.0, max_interval=7.0)

# グローバルなChromeドライバー
_driver: Optional[uc.Chrome] = None
_driver_lock = threading.Lock()

def get_driver():
    """Chromeドライバーを取得（シングルトンパターン）"""
    global _driver
    with _driver_lock:
        if _driver is None:
            print("🚀 Chrome WebDriver初期化中...")
            options = uc.ChromeOptions()
            options.headless = False
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            _driver = uc.Chrome(options=options, use_subprocess=False, version_main=None)
            print("✓ Chrome WebDriver初期化完了")
        return _driver


def scrape_horse_details(horse_url: str):
    """馬詳細ページから血統情報と過去成績を取得"""
    try:
        driver = get_driver()
        full_url = f'https://db.netkeiba.com{horse_url}' if horse_url.startswith('/') else horse_url
        
        print(f"  → 馬詳細取得: {full_url}")
        driver.get(full_url)
        time.sleep(random.uniform(1.5, 2.5))
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        details = {}
        
        # 基本情報テーブル
        profile_table = soup.find('table', class_='db_prof_table')
        if profile_table:
            rows = profile_table.find_all('tr')
            for row in rows:
                th = row.find('th')
                td = row.find('td')
                if th and td:
                    key = th.text.strip()
                    value = td.text.strip()
                    
                    if '生年月日' in key:
                        details['birth_date'] = value
                    elif '調教師' in key:
                        details['trainer'] = value
                    elif '馬主' in key:
                        details['owner'] = value
                    elif '生産者' in key:
                        details['breeder'] = value
                    elif '産地' in key:
                        details['breeding_farm'] = value
        
        # 血統情報
        pedigree_table = soup.find('table', class_='blood_table')
        if pedigree_table:
            # 父馬
            sire = pedigree_table.find('a', href=re.compile(r'/horse/'))
            if sire:
                details['sire'] = sire.text.strip()
            
            # 母馬・母父馬も同様に取得可能
            all_horses = pedigree_table.find_all('a', href=re.compile(r'/horse/'))
            if len(all_horses) >= 2:
                details['dam'] = all_horses[1].text.strip()
            if len(all_horses) >= 3:
                details['damsire'] = all_horses[2].text.strip()
        
        # 過去成績サマリー
        record_table = soup.find('table', class_='db_h_race_results')
        if record_table:
            rows = record_table.find_all('tr')[1:6]  # 最新5レース
            past_performances = []
            for row in rows:
                cols = row.find_all('td')
                if len(cols) >= 12:
                    perf = {
                        'date': cols[0].text.strip(),
                        'venue': cols[1].text.strip(),
                        'race_name': cols[4].text.strip(),
                        'finish': cols[11].text.strip(),
                        'jockey': cols[12].text.strip() if len(cols) > 12 else '',
                    }
                    past_performances.append(perf)
            details['past_performances'] = past_performances
        
        print(f"    ✓ 馬詳細取得完了: {len(details)}項目")
        return details
        
    except Exception as e:
        print(f"    ✗ 馬詳細取得エラー: {e}")
        return {}


def scrape_jockey_details(jockey_url: str):
    """騎手詳細ページから勝率等の統計情報を取得"""
    try:
        driver = get_driver()
        full_url = f'https://db.netkeiba.com{jockey_url}' if jockey_url.startswith('/') else jockey_url
        
        print(f"  → 騎手詳細取得: {full_url}")
        driver.get(full_url)
        time.sleep(random.uniform(1.5, 2.5))
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        details = {}
        
        # データ分析テーブル - 通算成績
        data_table = soup.find('table', class_='nk_tb_common')
        if data_table:
            # ヘッダーとデータ行を探す
            headers = data_table.find('thead')
            body = data_table.find('tbody')
            
            if headers and body:
                header_cols = [th.text.strip() for th in headers.find_all('th')]
                data_rows = body.find_all('tr')
                
                # 通算成績の行を探す
                for row in data_rows:
                    cols = row.find_all('td')
                    if cols and '通算' in cols[0].text:
                        # 勝率、連対率、複勝率を取得
                        for i, header in enumerate(header_cols):
                            if i < len(cols):
                                value = cols[i].text.strip()
                                if '勝率' in header:
                                    try:
                                        details['win_rate'] = float(value.replace('%', ''))
                                    except:
                                        pass
                                elif '連対率' in header:
                                    try:
                                        details['place_rate_top2'] = float(value.replace('%', ''))
                                    except:
                                        pass
                                elif '複勝率' in header:
                                    try:
                                        details['show_rate'] = float(value.replace('%', ''))
                                    except:
                                        pass
        
        # 通算成績が取れなかった場合、全ページのテキストから抽出
        if not details:
            page_text = soup.get_text()
            win_match = re.search(r'勝率[\s:：]*([0-9.]+)%', page_text)
            if win_match:
                details['win_rate'] = float(win_match.group(1))
            
            place_match = re.search(r'連対率[\s:：]*([0-9.]+)%', page_text)
            if place_match:
                details['place_rate_top2'] = float(place_match.group(1))
            
            show_match = re.search(r'複勝率[\s:：]*([0-9.]+)%', page_text)
            if show_match:
                details['show_rate'] = float(show_match.group(1))
        
        print(f"    ✓ 騎手詳細取得完了: {len(details)}項目")
        return details
        
    except Exception as e:
        print(f"    ✗ 騎手詳細取得エラー: {e}")
        return {}


def scrape_trainer_details(trainer_url: str):
    """調教師詳細ページから統計情報を取得"""
    try:
        driver = get_driver()
        full_url = f'https://db.netkeiba.com{trainer_url}' if trainer_url.startswith('/') else trainer_url
        
        print(f"  → 調教師詳細取得: {full_url}")
        driver.get(full_url)
        time.sleep(random.uniform(1.5, 2.5))
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        details = {}
        
        # データ分析テーブル - 通算成績
        data_table = soup.find('table', class_='nk_tb_common')
        if data_table:
            headers = data_table.find('thead')
            body = data_table.find('tbody')
            
            if headers and body:
                header_cols = [th.text.strip() for th in headers.find_all('th')]
                data_rows = body.find_all('tr')
                
                # 通算成績の行を探す
                for row in data_rows:
                    cols = row.find_all('td')
                    if cols and '通算' in cols[0].text:
                        for i, header in enumerate(header_cols):
                            if i < len(cols):
                                value = cols[i].text.strip()
                                if '勝率' in header:
                                    try:
                                        details['win_rate'] = float(value.replace('%', ''))
                                    except:
                                        pass
                                elif '連対率' in header:
                                    try:
                                        details['place_rate_top2'] = float(value.replace('%', ''))
                                    except:
                                        pass
                                elif '複勝率' in header:
                                    try:
                                        details['show_rate'] = float(value.replace('%', ''))
                                    except:
                                        pass
        
        # テキストから抽出
        if not details:
            page_text = soup.get_text()
            win_match = re.search(r'勝率[\s:：]*([0-9.]+)%', page_text)
            if win_match:
                details['win_rate'] = float(win_match.group(1))
            
            place_match = re.search(r'連対率[\s:：]*([0-9.]+)%', page_text)
            if place_match:
                details['place_rate_top2'] = float(place_match.group(1))
        
        print(f"    ✓ 調教師詳細取得完了: {len(details)}項目")
        return details
        
    except Exception as e:
        print(f"    ✗ 調教師詳細取得エラー: {e}")
        return {}


class EnhancedScrapeRequest(BaseModel):
    race_id: str
    include_details: bool = True  # 詳細ページも取得するか

class EnhancedScrapeResponse(BaseModel):
    success: bool
    race_info: dict = {}
    results: list[dict] = []
    lap_times: dict = {}
    corner_positions: dict = {}
    payouts: list[dict] = []
    error: str | None = None


@app.post("/scrape/enhanced", response_model=EnhancedScrapeResponse)
def scrape_race_enhanced(request: EnhancedScrapeRequest):
    """
    全特徴量を取得する拡張版スクレイピング
    """
    wait_time = rate_limiter.wait_if_needed()
    
    race_id = request.race_id
    url = f'https://race.netkeiba.com/race/result.html?race_id={race_id}'
    
    try:
        driver = get_driver()
        
        print(f"→ レース結果ページ取得: {url}")
        driver.get(url)
        time.sleep(random.uniform(2.0, 3.0))
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # ===== レース基本情報 =====
        race_info = {}
        
        # レース名
        race_name_elem = soup.find('h1', class_='RaceName')
        if race_name_elem:
            race_info['race_name'] = race_name_elem.text.strip()
        
        # RaceData01
        data01 = soup.find('div', class_='RaceData01')
        if data01:
            text = data01.text.strip()
            race_info['race_data_01'] = text
            
            # 発走時刻
            time_match = re.search(r'(\d+:\d+)発走', text)
            if time_match:
                race_info['post_time'] = time_match.group(1)
            
            # トラック種別
            if '芝' in text:
                race_info['track_type'] = '芝'
            elif 'ダート' in text or 'ダ' in text:
                race_info['track_type'] = 'ダート'
            
            # 距離
            dist_match = re.search(r'(\d+)m', text)
            if dist_match:
                race_info['distance'] = int(dist_match.group(1))
            
            # コース方向
            if '右' in text:
                race_info['course_direction'] = '右'
            elif '左' in text:
                race_info['course_direction'] = '左'
            
            # 天候
            weather_match = re.search(r'天候:([^\s/]+)', text)
            if weather_match:
                race_info['weather'] = weather_match.group(1)
            
            # 馬場状態
            field_match = re.search(r'馬場:([^\s]+)', text)
            if field_match:
                race_info['field_condition'] = field_match.group(1)
        
        # RaceData02
        data02 = soup.find('div', class_='RaceData02')
        if data02:
            text = data02.text.strip()
            race_info['race_data_02'] = text
            
            # 開催情報
            kaisai_match = re.search(r'(\d+)回\s+([^\s]+)\s+(\d+)日目', text)
            if kaisai_match:
                race_info['kai'] = int(kaisai_match.group(1))
                race_info['venue'] = kaisai_match.group(2)
                race_info['day'] = int(kaisai_match.group(3))
            
            # レースクラス
            for cls in ['オープン', '新馬', '未勝利', '１勝クラス', '1勝クラス', '２勝クラス', '2勝クラス', '３勝クラス', '3勝クラス']:
                if cls in text:
                    race_info['race_class'] = cls
                    break
            
            # 出走頭数
            head_match = re.search(r'(\d+)頭', text)
            if head_match:
                race_info['horse_count'] = int(head_match.group(1))
        
        # 賞金
        prize_elem = soup.find(string=re.compile('本賞金'))
        if prize_elem:
            race_info['prize_money'] = prize_elem.strip()
        
        print(f"✓ レース基本情報: {len(race_info)}項目")
        
        # ===== 結果テーブル（全15列） =====
        results = []
        result_table = soup.find('table', id='All_Result_Table')
        
        if not result_table:
            # idがない場合、内容から検索
            tables = soup.find_all('table')
            for table in tables:
                if '着順' in table.text and '馬名' in table.text:
                    result_table = table
                    break
        
        if result_table:
            rows = result_table.find_all('tr')[1:]  # ヘッダー除く
            
            for row in rows:
                cols = row.find_all('td')
                if len(cols) >= 15:
                    horse_data = {
                        'finish_position': cols[0].text.strip(),
                        'bracket_number': cols[1].text.strip(),
                        'horse_number': cols[2].text.strip(),
                    }
                    
                    # 馬名（リンク）
                    horse_link = cols[3].find('a')
                    if horse_link:
                        horse_data['horse_name'] = horse_link.text.strip()
                        horse_data['horse_url'] = horse_link.get('href', '')
                    else:
                        horse_data['horse_name'] = cols[3].text.strip()
                        horse_data['horse_url'] = ''
                    
                    horse_data['sex_age'] = cols[4].text.strip()
                    horse_data['jockey_weight'] = cols[5].text.strip()
                    
                    # 騎手（リンク）
                    jockey_link = cols[6].find('a')
                    if jockey_link:
                        horse_data['jockey_name'] = jockey_link.text.strip()
                        horse_data['jockey_url'] = jockey_link.get('href', '')
                    else:
                        horse_data['jockey_name'] = cols[6].text.strip()
                        horse_data['jockey_url'] = ''
                    
                    horse_data['finish_time'] = cols[7].text.strip()
                    horse_data['margin'] = cols[8].text.strip()
                    horse_data['popularity'] = cols[9].text.strip()
                    horse_data['odds'] = cols[10].text.strip()
                    horse_data['last_3f'] = cols[11].text.strip()
                    horse_data['corner_positions'] = cols[12].text.strip()
                    
                    # 調教師（リンク）
                    trainer_link = cols[13].find('a')
                    if trainer_link:
                        horse_data['trainer_name'] = trainer_link.text.strip()
                        horse_data['trainer_url'] = trainer_link.get('href', '')
                    else:
                        horse_data['trainer_name'] = cols[13].text.strip()
                        horse_data['trainer_url'] = ''
                    
                    horse_data['weight'] = cols[14].text.strip()
                    
                    # 詳細ページも取得する場合
                    if request.include_details:
                        if horse_data.get('horse_url'):
                            horse_details = scrape_horse_details(horse_data['horse_url'])
                            horse_data['horse_details'] = horse_details
                        
                        if horse_data.get('jockey_url'):
                            jockey_details = scrape_jockey_details(horse_data['jockey_url'])
                            horse_data['jockey_details'] = jockey_details
                        
                        if horse_data.get('trainer_url'):
                            trainer_details = scrape_trainer_details(horse_data['trainer_url'])
                            horse_data['trainer_details'] = trainer_details
                    
                    results.append(horse_data)
            
            print(f"✓ 結果テーブル: {len(results)}頭")
        
        # ===== ラップタイム =====
        lap_times = {}
        lap_table = soup.find('table', class_='Race_HaronTime')
        if lap_table:
            headers = lap_table.find('tr')
            if headers:
                distances = [th.text.strip() for th in headers.find_all(['th', 'td'])]
                times_row = lap_table.find_all('tr')[1] if len(lap_table.find_all('tr')) > 1 else None
                if times_row:
                    times = [td.text.strip() for td in times_row.find_all('td')]
                    for dist, t in zip(distances, times):
                        lap_times[dist] = t
            print(f"✓ ラップタイム: {len(lap_times)}地点")
        
        # ===== コーナー通過順位 =====
        corner_positions = {}
        corner_table = soup.find('table', class_='Corner_Num')
        if corner_table:
            rows = corner_table.find_all('tr')
            for row in rows:
                cols = row.find_all(['th', 'td'])
                if len(cols) >= 2:
                    corner = cols[0].text.strip()
                    order = cols[1].text.strip()
                    if corner and order:
                        corner_positions[corner] = order
            print(f"✓ コーナー通過: {len(corner_positions)}地点")
        
        # ===== 払戻 =====
        payouts = []
        payout_tables = soup.find_all('table', class_='Payout_Detail_Table')
        for table in payout_tables:
            rows = table.find_all('tr')
            for row in rows:
                cols = row.find_all(['th', 'td'])
                if len(cols) >= 3:
                    payout = {
                        'type': cols[0].text.strip(),
                        'numbers': cols[1].text.strip(),
                        'amount': cols[2].text.strip(),
                    }
                    payouts.append(payout)
        print(f"✓ 払戻: {len(payouts)}件")
        
        return EnhancedScrapeResponse(
            success=True,
            race_info=race_info,
            results=results,
            lap_times=lap_times,
            corner_positions=corner_positions,
            payouts=payouts
        )
        
    except Exception as e:
        print(f"✗ エラー: {e}")
        import traceback
        traceback.print_exc()
        return EnhancedScrapeResponse(success=False, error=str(e))


class RaceListRequest(BaseModel):
    kaisai_date: str  # YYYYMMDD形式

class RaceListResponse(BaseModel):
    success: bool
    race_ids: list[str] = []
    error: str | None = None


@app.post("/race_list", response_model=RaceListResponse)
def get_race_list(request: RaceListRequest):
    """指定日のrace_id一覧を取得"""
    kaisai_date = request.kaisai_date
    url = f'https://race.netkeiba.com/top/race_list.html?kaisai_date={kaisai_date}'
    
    print(f"📅 {kaisai_date[:4]}年{kaisai_date[4:6]}月{kaisai_date[6:8]}日のレース一覧取得中...")
    
    try:
        driver = get_driver()
        driver.get(url)
        time.sleep(random.uniform(2.0, 3.0))
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        race_ids = []
        for link in soup.find_all('a', href=True):
            href = link['href']
            match = re.search(r'race_id=(\d{12})', href)
            if match:
                race_id = match.group(1)
                if race_id not in race_ids:
                    race_ids.append(race_id)
        
        print(f"✓ {len(race_ids)}件のレースを取得")
        
        return RaceListResponse(success=True, race_ids=race_ids)
        
    except Exception as e:
        print(f"✗ エラー: {e}")
        return RaceListResponse(success=False, error=str(e))


@app.get("/health")
def health_check():
    """ヘルスチェック"""
    return {
        "status": "ok",
        "request_count": rate_limiter.request_count,
        "uptime_seconds": (datetime.now() - rate_limiter.start_time).total_seconds(),
        "driver_initialized": _driver is not None
    }


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
    print("全特徴量取得対応 拡張版スクレイピングサービス起動")
    print("=" * 80)
    print("機能:")
    print("  - レース基本情報（15項目以上）")
    print("  - 結果テーブル全15列")
    print("  - 馬詳細（血統、過去成績）")
    print("  - 騎手詳細（勝率、連対率、複勝率）")
    print("  - 調教師詳細（勝率等）")
    print("  - ラップタイム")
    print("  - コーナー通過順位")
    print("=" * 80)
    uvicorn.run(app, host='0.0.0.0', port=8001)
