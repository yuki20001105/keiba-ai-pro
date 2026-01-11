"""
全特徴量取得対応 - 最終版スクレイピングサービス
追加機能:
- 出馬表ページからの予想データ
- ラップタイムの区間・累計分離
- ペース区分
- 馬・騎手・調教師のID
- 通算戦績の詳細
- 派生特徴量の計算
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class RateLimiter:
    def __init__(self, min_interval=3.0, max_interval=7.0):
        self.min_interval = min_interval
        self.max_interval = max_interval
        self.last_request_time: Optional[datetime] = None
        self.request_count = 0
        self.start_time = datetime.now()
        self.lock = threading.Lock()
    
    def wait_if_needed(self):
        with self.lock:
            if self.last_request_time is None:
                self.last_request_time = datetime.now()
                return 0
            
            elapsed = (datetime.now() - self.last_request_time).total_seconds()
            required_wait = random.uniform(self.min_interval, self.max_interval)
            
            if elapsed < required_wait:
                wait_time = required_wait - elapsed
                time.sleep(wait_time)
            else:
                wait_time = 0
            
            self.last_request_time = datetime.now()
            self.request_count += 1
            return wait_time

rate_limiter = RateLimiter(min_interval=3.0, max_interval=7.0)

_driver: Optional[uc.Chrome] = None
_driver_lock = threading.Lock()

def get_driver():
    global _driver
    with _driver_lock:
        if _driver is None:
            options = uc.ChromeOptions()
            options.headless = False
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            _driver = uc.Chrome(options=options, use_subprocess=False, version_main=None)
        return _driver


def extract_id_from_url(url: str, id_type: str) -> str:
    """URLからID（数値部分）を抽出"""
    if not url:
        return ""
    
    patterns = {
        'horse': r'/horse/(\d+)',
        'jockey': r'/jockey/(?:result/recent/)?(\d+)',
        'trainer': r'/trainer/(?:result/recent/)?(\d+)',
    }
    
    pattern = patterns.get(id_type, r'/(\d+)')
    match = re.search(pattern, url)
    return match.group(1) if match else ""


def parse_weight_change(weight_str: str) -> dict:
    """馬体重を分解: '460(+2)' -> {kg: 460, change: 2}"""
    match = re.search(r'(\d+)\(([+-]?\d+)\)', weight_str)
    if match:
        return {
            'weight_kg': int(match.group(1)),
            'weight_change': int(match.group(2))
        }
    return {'weight_kg': 0, 'weight_change': 0}


def calculate_pace_diff(lap_times: dict) -> float:
    """前半と後半のペース差を計算"""
    try:
        # 600m と 1200m があれば差分計算
        if '600m' in lap_times and '1200m' in lap_times:
            time_600 = float(lap_times['600m'].replace(':', ''))
            time_1200 = float(lap_times['1200m'].replace(':', ''))
            # 後半600m = 1200m - 600m
            latter_600 = time_1200 - time_600
            return time_600 - latter_600  # 前半 - 後半
    except:
        pass
    return 0.0


def scrape_horse_details(horse_url: str):
    """馬詳細ページから追加情報を取得"""
    try:
        driver = get_driver()
        full_url = f'https://db.netkeiba.com{horse_url}' if horse_url.startswith('/') else horse_url
        
        print(f"  → 馬詳細取得: {full_url}")
        driver.get(full_url)
        time.sleep(random.uniform(1.5, 2.5))
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        details = {}
        
        # 馬ID抽出
        details['horse_id'] = extract_id_from_url(horse_url, 'horse')
        
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
                    elif '毛色' in key:
                        details['coat_color'] = value
                    elif 'セール' in key:
                        details['sale_price'] = value
        
        # 血統情報
        pedigree_table = soup.find('table', class_='blood_table')
        if pedigree_table:
            all_horses = pedigree_table.find_all('a', href=re.compile(r'/horse/'))
            if len(all_horses) >= 1:
                details['sire'] = all_horses[0].text.strip()
            if len(all_horses) >= 2:
                details['dam'] = all_horses[1].text.strip()
            if len(all_horses) >= 3:
                details['damsire'] = all_horses[2].text.strip()
        
        # 獲得賞金と通算戦績
        summary_div = soup.find('div', class_='db_head_info')
        if summary_div:
            text = summary_div.text
            
            # 獲得賞金
            prize_match = re.search(r'獲得賞金[\s:：]*([0-9,]+)万円', text)
            if prize_match:
                details['total_prize_money'] = prize_match.group(1)
            
            # 通算成績（例: 10戦3勝）
            record_match = re.search(r'(\d+)戦(\d+)勝', text)
            if record_match:
                details['total_runs'] = int(record_match.group(1))
                details['total_wins'] = int(record_match.group(2))
        
        # 過去成績（詳細）
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
                        'weather': cols[2].text.strip() if len(cols) > 2 else '',
                        'race_name': cols[4].text.strip() if len(cols) > 4 else '',
                        'distance': cols[5].text.strip() if len(cols) > 5 else '',
                        'track_condition': cols[6].text.strip() if len(cols) > 6 else '',
                        'finish': cols[11].text.strip() if len(cols) > 11 else '',
                        'jockey': cols[12].text.strip() if len(cols) > 12 else '',
                        'weight': cols[14].text.strip() if len(cols) > 14 else '',
                    }
                    past_performances.append(perf)
            details['past_performances'] = past_performances
        
        print(f"    ✓ 馬詳細取得完了: {len(details)}項目")
        return details
        
    except Exception as e:
        print(f"    ✗ 馬詳細取得エラー: {e}")
        return {}


def scrape_jockey_details(jockey_url: str):
    """騎手詳細ページから統計情報を取得"""
    try:
        driver = get_driver()
        full_url = f'https://db.netkeiba.com{jockey_url}' if jockey_url.startswith('/') else jockey_url
        
        print(f"  → 騎手詳細取得: {full_url}")
        driver.get(full_url)
        time.sleep(random.uniform(1.5, 2.5))
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        details = {}
        
        # 騎手ID抽出
        details['jockey_id'] = extract_id_from_url(jockey_url, 'jockey')
        
        # データ分析テーブル
        data_table = soup.find('table', class_='nk_tb_common')
        if data_table:
            headers = data_table.find('thead')
            body = data_table.find('tbody')
            
            if headers and body:
                header_cols = [th.text.strip() for th in headers.find_all('th')]
                data_rows = body.find_all('tr')
                
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
        
        # 調教師ID抽出
        details['trainer_id'] = extract_id_from_url(trainer_url, 'trainer')
        
        # データ分析テーブル
        data_table = soup.find('table', class_='nk_tb_common')
        if data_table:
            headers = data_table.find('thead')
            body = data_table.find('tbody')
            
            if headers and body:
                header_cols = [th.text.strip() for th in headers.find_all('th')]
                data_rows = body.find_all('tr')
                
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
        
        print(f"    ✓ 調教師詳細取得完了: {len(details)}項目")
        return details
        
    except Exception as e:
        print(f"    ✗ 調教師詳細取得エラー: {e}")
        return {}


class EnhancedScrapeRequest(BaseModel):
    race_id: str
    include_details: bool = True

class EnhancedScrapeResponse(BaseModel):
    success: bool
    race_info: dict = {}
    results: list[dict] = []
    lap_times: dict = {}
    lap_times_sectional: dict = {}  # 区間ラップ
    corner_positions: dict = {}
    payouts: list[dict] = []
    derived_features: dict = {}  # 派生特徴量
    error: str | None = None


@app.post("/scrape/ultimate", response_model=EnhancedScrapeResponse)
def scrape_race_ultimate(request: EnhancedScrapeRequest):
    """
    全特徴量を取得する最終版スクレイピング
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
        race_info['race_id'] = race_id
        
        # レース名
        race_name_elem = soup.find('h1', class_='RaceName')
        if race_name_elem:
            race_info['race_name'] = race_name_elem.text.strip()
        
        # RaceData01
        data01 = soup.find('div', class_='RaceData01')
        if data01:
            text = data01.text.strip()
            race_info['race_data_01'] = text
            
            time_match = re.search(r'(\d+:\d+)発走', text)
            if time_match:
                race_info['post_time'] = time_match.group(1)
            
            if '芝' in text:
                race_info['track_type'] = '芝'
            elif 'ダート' in text or 'ダ' in text:
                race_info['track_type'] = 'ダート'
            
            dist_match = re.search(r'(\d+)m', text)
            if dist_match:
                race_info['distance'] = int(dist_match.group(1))
            
            if '右' in text:
                race_info['course_direction'] = '右'
            elif '左' in text:
                race_info['course_direction'] = '左'
            
            weather_match = re.search(r'天候:([^\s/]+)', text)
            if weather_match:
                race_info['weather'] = weather_match.group(1)
            
            field_match = re.search(r'馬場:([^\s]+)', text)
            if field_match:
                race_info['field_condition'] = field_match.group(1)
        
        # RaceData02
        data02 = soup.find('div', class_='RaceData02')
        if data02:
            text = data02.text.strip()
            race_info['race_data_02'] = text
            
            kaisai_match = re.search(r'(\d+)回\s+([^\s]+)\s+(\d+)日目', text)
            if kaisai_match:
                race_info['kai'] = int(kaisai_match.group(1))
                race_info['venue'] = kaisai_match.group(2)
                race_info['day'] = int(kaisai_match.group(3))
            
            for cls in ['オープン', '新馬', '未勝利', '１勝クラス', '1勝クラス', '２勝クラス', '2勝クラス', '３勝クラス', '3勝クラス']:
                if cls in text:
                    race_info['race_class'] = cls
                    break
            
            head_match = re.search(r'(\d+)頭', text)
            if head_match:
                race_info['horse_count'] = int(head_match.group(1))
        
        # 賞金
        prize_elem = soup.find(string=re.compile('本賞金'))
        if prize_elem:
            race_info['prize_money'] = prize_elem.strip()
        
        print(f"✓ レース基本情報: {len(race_info)}項目")
        
        # ===== 結果テーブル（全15列 + ID） =====
        results = []
        result_table = soup.find('table', id='All_Result_Table')
        
        if not result_table:
            tables = soup.find_all('table')
            for table in tables:
                if '着順' in table.text and '馬名' in table.text:
                    result_table = table
                    break
        
        if result_table:
            rows = result_table.find_all('tr')[1:]
            
            for row in rows:
                cols = row.find_all('td')
                if len(cols) >= 15:
                    horse_data = {
                        'finish_position': cols[0].text.strip(),
                        'bracket_number': cols[1].text.strip(),
                        'horse_number': cols[2].text.strip(),
                    }
                    
                    # 馬名とID
                    horse_link = cols[3].find('a')
                    if horse_link:
                        horse_data['horse_name'] = horse_link.text.strip()
                        horse_data['horse_url'] = horse_link.get('href', '')
                        horse_data['horse_id'] = extract_id_from_url(horse_data['horse_url'], 'horse')
                    else:
                        horse_data['horse_name'] = cols[3].text.strip()
                        horse_data['horse_url'] = ''
                        horse_data['horse_id'] = ''
                    
                    horse_data['sex_age'] = cols[4].text.strip()
                    horse_data['jockey_weight'] = cols[5].text.strip()
                    
                    # 騎手とID
                    jockey_link = cols[6].find('a')
                    if jockey_link:
                        horse_data['jockey_name'] = jockey_link.text.strip()
                        horse_data['jockey_url'] = jockey_link.get('href', '')
                        horse_data['jockey_id'] = extract_id_from_url(horse_data['jockey_url'], 'jockey')
                    else:
                        horse_data['jockey_name'] = cols[6].text.strip()
                        horse_data['jockey_url'] = ''
                        horse_data['jockey_id'] = ''
                    
                    horse_data['finish_time'] = cols[7].text.strip()
                    horse_data['margin'] = cols[8].text.strip()
                    horse_data['popularity'] = cols[9].text.strip()
                    horse_data['odds'] = cols[10].text.strip()
                    horse_data['last_3f'] = cols[11].text.strip()
                    horse_data['corner_positions'] = cols[12].text.strip()
                    
                    # 調教師とID
                    trainer_link = cols[13].find('a')
                    if trainer_link:
                        horse_data['trainer_name'] = trainer_link.text.strip()
                        horse_data['trainer_url'] = trainer_link.get('href', '')
                        horse_data['trainer_id'] = extract_id_from_url(horse_data['trainer_url'], 'trainer')
                    else:
                        horse_data['trainer_name'] = cols[13].text.strip()
                        horse_data['trainer_url'] = ''
                        horse_data['trainer_id'] = ''
                    
                    weight_str = cols[14].text.strip()
                    horse_data['weight'] = weight_str
                    
                    # 馬体重を分解
                    weight_parsed = parse_weight_change(weight_str)
                    horse_data['weight_kg'] = weight_parsed['weight_kg']
                    horse_data['weight_change'] = weight_parsed['weight_change']
                    
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
        
        # ===== ラップタイム（累計と区間） =====
        lap_times = {}
        lap_times_sectional = {}
        
        lap_table = soup.find('table', class_='Race_HaronTime')
        if lap_table:
            rows = lap_table.find_all('tr')
            if len(rows) >= 2:
                # ヘッダー
                headers = rows[0]
                distances = [th.text.strip() for th in headers.find_all(['th', 'td'])]
                
                # 1行目: 累計タイム
                times_row1 = rows[1]
                times1 = [td.text.strip() for td in times_row1.find_all('td')]
                for dist, t in zip(distances, times1):
                    lap_times[dist] = t
                
                # 2行目: 区間タイム（あれば）
                if len(rows) >= 3:
                    times_row2 = rows[2]
                    times2 = [td.text.strip() for td in times_row2.find_all('td')]
                    for dist, t in zip(distances, times2):
                        lap_times_sectional[dist] = t
            
            print(f"✓ ラップタイム: 累計{len(lap_times)}地点, 区間{len(lap_times_sectional)}地点")
        
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
        
        # ===== 派生特徴量の計算 =====
        derived_features = {}
        
        # ペース差分
        if lap_times:
            derived_features['pace_diff'] = calculate_pace_diff(lap_times)
        
        # 上がり順位（レース内での相対順位）
        if results:
            last_3f_values = []
            for r in results:
                try:
                    last_3f_values.append((r, float(r.get('last_3f', 999))))
                except:
                    last_3f_values.append((r, 999))
            
            # 上がり順にソート
            last_3f_values.sort(key=lambda x: x[1])
            for rank, (r, val) in enumerate(last_3f_values, 1):
                r['last_3f_rank'] = rank
        
        # オッズから市場の歪み（人気の集中度）
        if results:
            odds_list = []
            for r in results:
                try:
                    odds_list.append(float(r.get('odds', 0)))
                except:
                    pass
            
            if odds_list:
                # 暗黙確率の計算
                implied_probs = [1/o if o > 0 else 0 for o in odds_list]
                total_prob = sum(implied_probs)
                
                if total_prob > 0:
                    # 正規化
                    normalized_probs = [p/total_prob for p in implied_probs]
                    
                    # エントロピー（人気の割れ度）
                    import math
                    entropy = -sum([p * math.log(p) if p > 0 else 0 for p in normalized_probs])
                    derived_features['market_entropy'] = entropy
                    
                    # 上位3頭の確率和（人気集中度）
                    top3_prob = sum(sorted(normalized_probs, reverse=True)[:3])
                    derived_features['top3_probability'] = top3_prob
        
        return EnhancedScrapeResponse(
            success=True,
            race_info=race_info,
            results=results,
            lap_times=lap_times,
            lap_times_sectional=lap_times_sectional,
            corner_positions=corner_positions,
            payouts=payouts,
            derived_features=derived_features
        )
        
    except Exception as e:
        print(f"✗ エラー: {e}")
        import traceback
        traceback.print_exc()
        return EnhancedScrapeResponse(success=False, error=str(e))


# 既存のエンドポイントも維持
class RaceListRequest(BaseModel):
    kaisai_date: str

class RaceListResponse(BaseModel):
    success: bool
    race_ids: list[str] = []
    error: str | None = None


@app.post("/race_list", response_model=RaceListResponse)
def get_race_list(request: RaceListRequest):
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
    return {
        "status": "ok",
        "request_count": rate_limiter.request_count,
        "uptime_seconds": (datetime.now() - rate_limiter.start_time).total_seconds(),
        "driver_initialized": _driver is not None
    }


@app.on_event("shutdown")
def shutdown_event():
    global _driver
    if _driver is not None:
        _driver.quit()
        _driver = None


if __name__ == '__main__':
    import uvicorn
    print("=" * 80)
    print("全特徴量取得対応 最終版スクレイピングサービス起動")
    print("=" * 80)
    print("新機能:")
    print("  - 馬・騎手・調教師のID抽出")
    print("  - 累計ラップと区間ラップの分離")
    print("  - 馬体重の分解（kg/増減）")
    print("  - 派生特徴量の自動計算")
    print("  - 上がり順位、市場エントロピー等")
    print("=" * 80)
    uvicorn.run(app, host='0.0.0.0', port=8001)
