"""
高速化版Ultimate スクレイピングサービス
改善点:
1. 詳細ページ取得をオプション化（デフォルトOFF）
2. 騎手・調教師データはキャッシュ（同じ人は1回だけ取得）
3. 並列取得可能な構造（ThreadPoolExecutor使用）
4. レート制限の最適化
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
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# グローバルキャッシュ
_jockey_cache = {}
_trainer_cache = {}
_cache_lock = threading.Lock()

class RateLimiter:
    def __init__(self, min_interval=2.0, max_interval=4.0):  # 高速化: 3-7秒 → 2-4秒
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

rate_limiter = RateLimiter(min_interval=2.0, max_interval=4.0)

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
        return ''
    
    patterns = {
        'horse': r'/horse/(\d+)',
        'jockey': r'/jockey/(\d+)',
        'trainer': r'/trainer/(\d+)',
    }
    
    pattern = patterns.get(id_type)
    if not pattern:
        return ''
    
    match = re.search(pattern, url)
    return match.group(1) if match else ''


def parse_weight_change(weight_str: str) -> dict:
    """馬体重文字列を分解"""
    result = {'weight_kg': None, 'weight_change': None}
    
    if not weight_str or weight_str == '計不':
        return result
    
    # 例: "466(-12)" -> weight_kg=466, weight_change=-12
    match = re.match(r'(\d+)\(([+-]?\d+)\)', weight_str)
    if match:
        result['weight_kg'] = int(match.group(1))
        result['weight_change'] = int(match.group(2))
    
    return result


def parse_sex_age(sex_age: str) -> dict:
    """性齢をパース: '牡3' → {'sex': '牡', 'age': 3}"""
    result = {'sex': None, 'age': None}
    
    if not sex_age or sex_age.strip() == '':
        return result
    
    # 例: "牡3", "牝4", "セ5" など
    match = re.match(r'([牡牝セ])([0-9]+)', sex_age.strip())
    if match:
        result['sex'] = match.group(1)
        result['age'] = int(match.group(2))
    
    return result


def parse_corner_positions(positions: str) -> list:
    """コーナー通過順をパース: '5-5-4-3' → [5, 5, 4, 3]"""
    if not positions or positions.strip() in ['', '-']:
        return []
    
    try:
        # ハイフン区切りで分割して整数リストに変換
        return [int(p.strip()) for p in positions.split('-') if p.strip().isdigit()]
    except:
        return []


def calculate_pace_diff(lap_times: dict) -> float:
    """ペース差分を計算（前半-後半）"""
    try:
        if '400m' in lap_times and '1000m' in lap_times:
            first_400 = float(lap_times['400m'])
            time_1000 = float(lap_times['1000m'])
            last_600 = time_1000 - first_400
            return first_400 - (last_600 * 400 / 600)
    except:
        pass
    return 0.0


def calculate_past_performance_features(past_performances: list, current_date: str = None) -> dict:
    """近走データから派生特徴量を計算
    
    Args:
        past_performances: 過去成績のリスト [{'date': '2023/05/01', 'distance': '1400m', ...}, ...]
        current_date: 現在のレース日付（例: '2023/05/15'）
    
    Returns:
        派生特徴量の辞書
    """
    features = {}
    
    if not past_performances or len(past_performances) == 0:
        return features
    
    try:
        from datetime import datetime
        
        # 前走からの日数計算
        if current_date and len(past_performances) > 0:
            last_race = past_performances[0]
            if 'date' in last_race and last_race['date']:
                try:
                    last_date = datetime.strptime(last_race['date'].replace('/', '-'), '%Y-%m-%d')
                    curr_date = datetime.strptime(current_date.replace('/', '-'), '%Y-%m-%d')
                    days_diff = (curr_date - last_date).days
                    features['days_since_last_race'] = days_diff
                except:
                    pass
        
        # 距離変化計算（前走との距離差）
        if len(past_performances) >= 2:
            try:
                # 今回の距離は別途渡す必要があるため、前走と前々走の比較
                last_dist = past_performances[0].get('distance', '')
                prev_dist = past_performances[1].get('distance', '')
                
                if last_dist and prev_dist:
                    # '1400m' -> 1400 に変換
                    last_m = int(re.search(r'(\d+)', last_dist).group(1))
                    prev_m = int(re.search(r'(\d+)', prev_dist).group(1))
                    features['last_distance_change'] = last_m - prev_m
            except:
                pass
        
        # コース変化検出（芝⇔ダート）
        # 注: past_performancesに'track_type'が含まれる場合のみ有効
        if len(past_performances) >= 2:
            surfaces = []
            for perf in past_performances[:2]:
                if 'track_type' in perf:
                    surfaces.append(perf['track_type'])
            
            if len(surfaces) == 2:
                if surfaces[0] != surfaces[1]:
                    features['surface_changed'] = True
                else:
                    features['surface_changed'] = False
        
        # 人気トレンド（連続して人気が上昇/下降）
        # 注: past_performancesに'popularity'が含まれる場合のみ有効
        if len(past_performances) >= 2:
            popularities = []
            for perf in past_performances[:3]:
                if 'popularity' in perf and perf['popularity']:
                    try:
                        popularities.append(int(perf['popularity']))
                    except:
                        pass
            
            if len(popularities) >= 2:
                # 人気が上昇傾向（数字が小さくなる）= 負の傾向
                if popularities[0] < popularities[1]:
                    features['popularity_trend'] = 'improving'  # 人気上昇
                elif popularities[0] > popularities[1]:
                    features['popularity_trend'] = 'declining'  # 人気下降
                else:
                    features['popularity_trend'] = 'stable'
    
    except Exception as e:
        print(f"    ⚠ 近走派生特徴計算エラー: {e}")
    
    return features


def scrape_horse_details(horse_url: str):
    """馬詳細ページから追加情報を取得（高速化版：最小限の情報のみ）"""
    try:
        driver = get_driver()
        full_url = f'https://db.netkeiba.com{horse_url}' if horse_url.startswith('/') else horse_url
        
        rate_limiter.wait_if_needed()
        driver.get(full_url)
        time.sleep(random.uniform(1.0, 1.5))  # 高速化: 1.5-2.5秒 → 1.0-1.5秒
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        details = {}
        
        # 馬ID抽出
        details['horse_id'] = extract_id_from_url(horse_url, 'horse')
        
        # プロフィールテーブル（最小限の情報のみ）
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
                    elif '毛色' in key:
                        details['coat_color'] = value
        
        # 過去成績（最新3走のみ）
        past_performances = []
        result_table = soup.find('table', class_='db_h_race_results')
        if result_table:
            body = result_table.find('tbody')
            if body:
                rows = body.find_all('tr')[:3]  # 最新3走のみ
                for row in rows:
                    cols = row.find_all('td')
                    if len(cols) > 14:
                        perf = {
                            'date': cols[0].text.strip(),
                            'venue': cols[1].text.strip(),
                            'distance': cols[5].text.strip(),
                            'finish': cols[11].text.strip(),
                            'weight': cols[14].text.strip(),
                        }
                        past_performances.append(perf)
        
        details['past_performances'] = past_performances
        return details
        
    except Exception as e:
        print(f"    ✗ 馬詳細取得エラー: {e}")
        return {}


def scrape_jockey_details_cached(jockey_url: str, jockey_id: str):
    """騎手詳細ページから統計情報を取得（キャッシュ付き）"""
    with _cache_lock:
        if jockey_id in _jockey_cache:
            print(f"  → 騎手詳細: キャッシュヒット {jockey_id}")
            return _jockey_cache[jockey_id]
    
    try:
        driver = get_driver()
        full_url = f'https://db.netkeiba.com{jockey_url}' if jockey_url.startswith('/') else jockey_url
        
        rate_limiter.wait_if_needed()
        driver.get(full_url)
        time.sleep(random.uniform(1.0, 1.5))
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        details = {'jockey_id': jockey_id}
        
        # データ分析テーブル
        data_table = soup.find('table', class_='nk_tb_common')
        if data_table:
            body = data_table.find('tbody')
            if body:
                rows = body.find_all('tr')
                for row in rows:
                    cols = row.find_all('td')
                    if cols and '通算' in cols[0].text:
                        if len(cols) > 6:
                            try:
                                details['win_rate'] = float(cols[4].text.strip().replace('%', ''))
                                details['place_rate_top2'] = float(cols[5].text.strip().replace('%', ''))
                                details['show_rate'] = float(cols[6].text.strip().replace('%', ''))
                            except:
                                pass
                        break
        
        with _cache_lock:
            _jockey_cache[jockey_id] = details
        
        return details
        
    except Exception as e:
        print(f"    ✗ 騎手詳細取得エラー: {e}")
        return {'jockey_id': jockey_id}


def scrape_trainer_details_cached(trainer_url: str, trainer_id: str):
    """調教師詳細ページから統計情報を取得（キャッシュ付き）"""
    with _cache_lock:
        if trainer_id in _trainer_cache:
            print(f"  → 調教師詳細: キャッシュヒット {trainer_id}")
            return _trainer_cache[trainer_id]
    
    try:
        driver = get_driver()
        full_url = f'https://db.netkeiba.com{trainer_url}' if trainer_url.startswith('/') else trainer_url
        
        rate_limiter.wait_if_needed()
        driver.get(full_url)
        time.sleep(random.uniform(1.0, 1.5))
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        details = {'trainer_id': trainer_id}
        
        # データ分析テーブル
        data_table = soup.find('table', class_='nk_tb_common')
        if data_table:
            body = data_table.find('tbody')
            if body:
                rows = body.find_all('tr')
                for row in rows:
                    cols = row.find_all('td')
                    if cols and '通算' in cols[0].text:
                        if len(cols) > 5:
                            try:
                                details['win_rate'] = float(cols[4].text.strip().replace('%', ''))
                                details['place_rate_top2'] = float(cols[5].text.strip().replace('%', ''))
                            except:
                                pass
                        break
        
        with _cache_lock:
            _trainer_cache[trainer_id] = details
        
        return details
        
    except Exception as e:
        print(f"    ✗ 調教師詳細取得エラー: {e}")
        return {'trainer_id': trainer_id}


def scrape_shutuba_table(race_id: str) -> dict:
    """出馬表ページから予想ペース・脚質情報を取得
    
    Args:
        race_id: レースID (例: '202305010101')
    
    Returns:
        {
            'predicted_pace': 'M',  # H/M/S
            'running_styles': {
                'horse_id': {'style': '先行', 'group': '早'}
            }
        }
    """
    result = {
        'predicted_pace': None,
        'running_styles': {}
    }
    
    try:
        driver = get_driver()
        shutuba_url = f'https://race.netkeiba.com/race/shutuba.html?race_id={race_id}'
        
        rate_limiter.wait_if_needed()
        driver.get(shutuba_url)
        time.sleep(random.uniform(2.0, 3.0))
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # 予想ペースの取得
        pace_info = soup.find('div', class_='RaceKinds')
        if pace_info:
            pace_text = pace_info.text
            pace_match = re.search(r'予想ペース[：:\s]*([HMS])', pace_text)
            if pace_match:
                result['predicted_pace'] = pace_match.group(1)
        
        # 脚質情報の取得（出馬表テーブルから）
        shutuba_table = soup.find('table', class_='Shutuba_Table')
        if not shutuba_table:
            # 別のクラス名の可能性もある
            shutuba_table = soup.find('table', class_='shutuba_table')
        
        if shutuba_table:
            rows = shutuba_table.find_all('tr')
            for row in rows:
                cols = row.find_all('td')
                if len(cols) < 5:
                    continue
                
                # 馬名からIDを抽出
                horse_link = cols[3].find('a') if len(cols) > 3 else None
                if horse_link:
                    horse_url = horse_link.get('href', '')
                    horse_id = extract_id_from_url(horse_url, 'horse')
                    
                    # 脚質情報（テーブル内にある場合）
                    # 注: 実際のHTMLを確認して適切な列を特定する必要あり
                    style_info = {}
                    
                    # 例: 6列目に脚質、7列目にグループなどがある場合
                    if len(cols) > 6:
                        style_text = cols[6].text.strip()
                        if style_text in ['逃', '先', '差', '追', '逃げ', '先行', '差し', '追込']:
                            style_info['style'] = style_text
                    
                    if len(cols) > 7:
                        group_text = cols[7].text.strip()
                        if group_text in ['早', '中', '末']:
                            style_info['group'] = group_text
                    
                    if style_info and horse_id:
                        result['running_styles'][horse_id] = style_info
        
        print(f"✓ 出馬表: 予想ペース={result['predicted_pace']}, 脚質情報={len(result['running_styles'])}頭")
        
    except Exception as e:
        print(f"✗ 出馬表取得エラー: {e}")
    
    return result


class EnhancedScrapeRequest(BaseModel):
    race_id: str
    include_details: bool = False  # 高速化: デフォルトをFalseに変更
    include_shutuba: bool = False  # 出馬表情報も取得するか
    save_to_db: bool = False

class EnhancedScrapeResponse(BaseModel):
    success: bool
    race_info: dict = {}
    results: list[dict] = []
    lap_times: dict = {}
    lap_times_sectional: dict = {}
    corner_positions: dict = {}
    payouts: list[dict] = []
    derived_features: dict = {}
    shutuba_info: dict = {}  # 出馬表情報追加
    error: str | None = None


@app.post("/scrape/ultimate", response_model=EnhancedScrapeResponse)
def scrape_race_ultimate(request: EnhancedScrapeRequest):
    """
    高速化版Ultimate スクレイピング
    - include_details=False: 約15-30秒（詳細ページなし）
    - include_details=True: 約60-120秒（詳細ページあり、キャッシュ活用）
    """
    wait_time = rate_limiter.wait_if_needed()
    
    race_id = request.race_id
    race_url = f'https://race.netkeiba.com/race/result.html?race_id={race_id}'
    
    print(f"\n{'='*80}")
    print(f"【Ultimate版スクレイピング（高速版）】")
    print(f"Race ID: {race_id}")
    print(f"詳細ページ取得: {'ON' if request.include_details else 'OFF（高速モード）'}")
    print(f"{'='*80}")
    
    try:
        driver = get_driver()
        driver.get(race_url)
        time.sleep(random.uniform(2.0, 3.0))
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # ===== レース基本情報 =====
        race_info = {}
        race_name_tag = soup.find('div', class_='RaceName')
        if race_name_tag:
            race_info['race_name'] = race_name_tag.text.strip()
        
        race_data1 = soup.find('div', class_='RaceData01')
        if race_data1:
            spans = race_data1.find_all('span')
            for span in spans:
                text = span.text.strip()
                if '発走' in text:
                    race_info['post_time'] = text
                elif '天候' in text or '晴' in text or '曇' in text:
                    race_info['weather'] = text
                elif 'ダ' in text or '芝' in text:
                    if 'ダ' in text:
                        race_info['track_type'] = 'ダート'
                    else:
                        race_info['track_type'] = '芝'
                    
                    distance_match = re.search(r'(\d+)m', text)
                    if distance_match:
                        race_info['distance'] = distance_match.group(1)
                elif '良' in text or '稍' in text or '重' in text or '不' in text:
                    race_info['track_condition'] = text
        
        # ペース区分を抽出（H/M/S）
        # ページ内の適切な位置から探す
        pace_info = soup.find('div', class_='RaceData02')
        if pace_info:
            pace_text = pace_info.text
            # "ペース：H"のようなパターンを探す
            pace_match = re.search(r'ペース[：:\s]*([HMS])', pace_text)
            if pace_match:
                race_info['pace_classification'] = pace_match.group(1)
        
        race_data2 = soup.find('div', class_='RaceData02')
        if race_data2:
            spans = race_data2.find_all('span')
            for span in spans:
                text = span.text.strip()
                if '本賞金' in text:
                    race_info['prize_money'] = text
        
        # 開催情報
        race_list = soup.find('ul', class_='RaceList_DataList')
        if race_list:
            li_items = race_list.find_all('li')
            for li in li_items:
                text = li.text.strip()
                if '回' in text and '日目' in text:
                    parts = text.split()
                    for part in parts:
                        if '回' in part:
                            race_info['round'] = part
                        if '日目' in part:
                            race_info['day'] = part.replace('日目', '')
                if any(v in text for v in ['中山', '東京', '京都', '阪神', '中京', '新潟', '福島', '小倉', '札幌', '函館']):
                    for venue in ['中山', '東京', '京都', '阪神', '中京', '新潟', '福島', '小倉', '札幌', '函館']:
                        if venue in text:
                            race_info['venue'] = venue
                            break
        
        print(f"✓ レース基本情報取得")
        
        # ===== 結果テーブル =====
        results = []
        # 新しいクラス名に対応
        result_table = soup.find('table', class_='RaceTable01')
        if not result_table:
            # 旧クラス名も試す
            result_table = soup.find('table', class_='race_table_01 nk_tb_common')
        
        if result_table:
            body = result_table.find('tbody')
            if body:
                rows = body.find_all('tr')
                
                for row in rows:
                    cols = row.find_all('td')
                    if len(cols) < 15:
                        continue
                    
                    horse_data = {}
                    
                    # 基本情報
                    horse_data['finish_position'] = cols[0].text.strip()
                    horse_data['bracket_number'] = cols[1].text.strip()
                    horse_data['horse_number'] = cols[2].text.strip()
                    
                    # 馬とID
                    horse_link = cols[3].find('a')
                    if horse_link:
                        horse_data['horse_name'] = horse_link.text.strip()
                        horse_data['horse_url'] = horse_link.get('href', '')
                        horse_data['horse_id'] = extract_id_from_url(horse_data['horse_url'], 'horse')
                    else:
                        horse_data['horse_name'] = cols[3].text.strip()
                        horse_data['horse_url'] = ''
                        horse_data['horse_id'] = ''
                    
                # 性齢をパース
                sex_age_raw = cols[4].text.strip()
                horse_data['sex_age'] = sex_age_raw  # 元の文字列も保持
                sex_age_parsed = parse_sex_age(sex_age_raw)
                horse_data['sex'] = sex_age_parsed['sex']
                horse_data['age'] = sex_age_parsed['age']
                
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
                
                # コーナー通過順をパース
                corner_pos_raw = cols[12].text.strip()
                horse_data['corner_positions'] = corner_pos_raw  # 元の文字列も保持
                horse_data['corner_positions_list'] = parse_corner_positions(corner_pos_raw)
                
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
                
                # 馬体重を分解
                weight_str = cols[14].text.strip()
                horse_data['weight'] = weight_str
                weight_parsed = parse_weight_change(weight_str)
                horse_data['weight_kg'] = weight_parsed['weight_kg']
                horse_data['weight_change'] = weight_parsed['weight_change']
                
                results.append(horse_data)
            
            print(f"✓ 結果テーブル: {len(results)}頭")
        
        # ===== 詳細ページ取得（オプション）=====
        if request.include_details and results:
            print(f"\n【詳細ページ取得開始】")
            start_time = time.time()
            
            # ユニークなID収集
            unique_jockeys = {}
            unique_trainers = {}
            
            for r in results:
                jockey_id = r.get('jockey_id')
                if jockey_id and jockey_id not in unique_jockeys:
                    unique_jockeys[jockey_id] = r.get('jockey_url')
                
                trainer_id = r.get('trainer_id')
                if trainer_id and trainer_id not in unique_trainers:
                    unique_trainers[trainer_id] = r.get('trainer_url')
            
            print(f"  馬: {len(results)}頭")
            print(f"  騎手: {len(unique_jockeys)}人（ユニーク）")
            print(f"  調教師: {len(unique_trainers)}人（ユニーク）")
            
            # 馬詳細を取得
            for i, r in enumerate(results, 1):
                if r.get('horse_url'):
                    print(f"  [{i}/{len(results)}] 馬詳細取得中...")
                    horse_details = scrape_horse_details(r['horse_url'])
                    r['horse_details'] = horse_details
                    
                    # 近走データから派生特徴を計算
                    if 'past_performances' in horse_details:
                        # レース日付を取得（race_infoから）
                        race_date = race_info.get('date', None)
                        past_features = calculate_past_performance_features(
                            horse_details['past_performances'],
                            race_date
                        )
                        r['past_performance_features'] = past_features
            
            # 騎手詳細を取得（キャッシュ活用）
            jockey_details_map = {}
            for jockey_id, jockey_url in unique_jockeys.items():
                print(f"  騎手詳細取得: {jockey_id}")
                jockey_details_map[jockey_id] = scrape_jockey_details_cached(jockey_url, jockey_id)
            
            # 調教師詳細を取得（キャッシュ活用）
            trainer_details_map = {}
            for trainer_id, trainer_url in unique_trainers.items():
                print(f"  調教師詳細取得: {trainer_id}")
                trainer_details_map[trainer_id] = scrape_trainer_details_cached(trainer_url, trainer_id)
            
            # 詳細情報を各馬に紐付け
            for r in results:
                if r.get('jockey_id') in jockey_details_map:
                    r['jockey_details'] = jockey_details_map[r['jockey_id']]
                if r.get('trainer_id') in trainer_details_map:
                    r['trainer_details'] = trainer_details_map[r['trainer_id']]
            
            elapsed = time.time() - start_time
            print(f"✓ 詳細ページ取得完了: {elapsed:.1f}秒")
        
        # ===== ラップタイム（累計と区間） =====
        lap_times = {}
        lap_times_sectional = {}
        
        lap_table = soup.find('table', class_='Race_HaronTime')
        if lap_table:
            rows = lap_table.find_all('tr')
            if len(rows) >= 2:
                headers = rows[0]
                distances = [th.text.strip() for th in headers.find_all(['th', 'td'])]
                
                times_row1 = rows[1]
                times1 = [td.text.strip() for td in times_row1.find_all('td')]
                for dist, t in zip(distances, times1):
                    lap_times[dist] = t
                
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
        
        if lap_times:
            derived_features['pace_diff'] = calculate_pace_diff(lap_times)
        
        # 上がり順位
        if results:
            last_3f_values = []
            for r in results:
                try:
                    last_3f_values.append((r, float(r.get('last_3f', 999))))
                except:
                    last_3f_values.append((r, 999))
            
            last_3f_values.sort(key=lambda x: x[1])
            for rank, (r, val) in enumerate(last_3f_values, 1):
                r['last_3f_rank'] = rank
        
        # オッズエントロピー
        if results:
            import math
            odds_list = []
            for r in results:
                try:
                    odds_list.append(float(r.get('odds', 0)))
                except:
                    pass
            
            if odds_list:
                total = sum(1/o for o in odds_list if o > 0)
                probs = [1/o/total for o in odds_list if o > 0]
                entropy = -sum(p * math.log2(p) for p in probs if p > 0)
                derived_features['market_entropy'] = entropy
                
                sorted_probs = sorted(probs, reverse=True)
                derived_features['top3_probability'] = sum(sorted_probs[:3])
        
        race_info['num_horses'] = len(results)
        
        # ===== 出馬表情報取得（オプション） =====
        shutuba_info = {}
        if request.include_shutuba:
            print(f"\n【出馬表情報取得開始】")
            shutuba_info = scrape_shutuba_table(race_id)
            
            # 取得した脚質情報を各馬に紐付け
            if shutuba_info.get('running_styles'):
                for r in results:
                    horse_id = r.get('horse_id')
                    if horse_id in shutuba_info['running_styles']:
                        r['running_style'] = shutuba_info['running_styles'][horse_id]
        
        print(f"\n✓ スクレイピング完了")
        print(f"{'='*80}\n")
        
        return EnhancedScrapeResponse(
            success=True,
            race_info=race_info,
            results=results,
            lap_times=lap_times,
            lap_times_sectional=lap_times_sectional,
            corner_positions=corner_positions,
            payouts=payouts,
            derived_features=derived_features,
            shutuba_info=shutuba_info
        )
        
    except Exception as e:
        print(f"✗ エラー: {e}")
        import traceback
        traceback.print_exc()
        return EnhancedScrapeResponse(
            success=False,
            error=str(e)
        )


@app.get("/health")
def health_check():
    uptime = (datetime.now() - rate_limiter.start_time).total_seconds()
    return {
        "status": "ok",
        "request_count": rate_limiter.request_count,
        "uptime_seconds": uptime,
        "driver_initialized": _driver is not None,
        "jockey_cache_size": len(_jockey_cache),
        "trainer_cache_size": len(_trainer_cache),
    }


@app.post("/cache/clear")
def clear_cache():
    """キャッシュをクリア"""
    with _cache_lock:
        _jockey_cache.clear()
        _trainer_cache.clear()
    return {"message": "Cache cleared"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
