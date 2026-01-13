#!/usr/bin/env python3
"""
完全版スクレイピング - Streamlit版と同じ構造
EUC-JP対応 + 全特徴量取得 + Supabase保存

使い方:
  python complete_scraper.py --date 20240106
  python complete_scraper.py --race 202406010101
  python complete_scraper.py --date 20240106 --save
"""
import argparse
import requests
from bs4 import BeautifulSoup
import re
import time
import random
from datetime import datetime
from typing import Optional


class CompleteScraper:
    """EUC-JP対応の完全版スクレイパー"""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
    
    def fetch_html(self, url: str) -> BeautifulSoup:
        """HTMLを取得（EUC-JP対応）"""
        response = self.session.get(url, timeout=30)
        response.encoding = 'EUC-JP'  # 重要！
        return BeautifulSoup(response.text, 'html.parser')
    
    def get_race_list(self, date: str) -> list[str]:
        """
        指定日のレース一覧を取得（Streamlit版のingest_by_dateと同じ）
        
        Args:
            date: YYYYMMDD形式
        
        Returns:
            レースIDのリスト
        """
        url = f'https://race.netkeiba.com/top/race_list.html?kaisai_date={date}'
        print(f"\n📅 {date} のレース一覧を取得中...")
        print(f"URL: {url}")
        
        soup = self.fetch_html(url)
        
        # レースIDを抽出
        race_ids = []
        links = soup.find_all('a', href=re.compile(r'race_id=(\d{12})'))
        for link in links:
            match = re.search(r'race_id=(\d{12})', link.get('href', ''))
            if match:
                race_id = match.group(1)
                if race_id not in race_ids:
                    race_ids.append(race_id)
        
        # サブページもチェック（current_group）
        group_ids = set(re.findall(r'current_group=(\d+)', str(soup)))
        for gid in sorted(group_ids):
            sub_url = f'https://race.netkeiba.com/top/race_list_sub.html?current_group={gid}&kaisai_date={date}'
            sub_soup = self.fetch_html(sub_url)
            sub_links = sub_soup.find_all('a', href=re.compile(r'race_id=(\d{12})'))
            for link in sub_links:
                match = re.search(r'race_id=(\d{12})', link.get('href', ''))
                if match:
                    race_id = match.group(1)
                    if race_id not in race_ids:
                        race_ids.append(race_id)
        
        print(f"✅ {len(race_ids)} レースを発見")
        return sorted(race_ids)
    
    def scrape_race(self, race_id: str) -> dict:
        """
        レースの全データをスクレイピング（Streamlit版のingest_one_raceと同じ）
        
        Returns:
            {
                'race_id': str,
                'race_name': str,
                'date': str,
                'venue': str,
                'race_class': str,
                'distance': int,
                'track_type': str,
                'weather': str,
                'field_condition': str,
                'horses': list[dict],
                'payouts': list[dict],
            }
        """
        url = f'https://race.netkeiba.com/race/result.html?race_id={race_id}'
        print(f"\n🏇 レース {race_id} をスクレイピング中...")
        print(f"URL: {url}")
        
        soup = self.fetch_html(url)
        
        result = {'race_id': race_id}
        
        # レース名
        race_name_tag = soup.find('h1', class_='RaceName')
        if race_name_tag:
            result['race_name'] = race_name_tag.text.strip()
        
        # 日付・会場を抽出
        # race_id の形式: YYYYMMDDVVRR
        # YYYY: 年, MM: 月, DD: 日, VV: 会場, RR: レース番号
        result['date'] = f"{race_id[:4]}-{race_id[4:6]}-{race_id[6:8]}"
        venue_code = race_id[8:10]
        venue_map = {
            '01': '札幌', '02': '函館', '03': '福島', '04': '新潟',
            '05': '東京', '06': '中山', '07': '中京', '08': '京都',
            '09': '阪神', '10': '小倉'
        }
        result['venue'] = venue_map.get(venue_code, f'会場{venue_code}')
        
        # レースデータ1（距離・馬場状態など）
        race_data1 = soup.find('div', class_='RaceData01')
        if race_data1:
            spans = race_data1.find_all('span')
            for span in spans:
                text = span.text.strip()
                
                # 距離
                distance_match = re.search(r'(\d+)m', text)
                if distance_match:
                    result['distance'] = int(distance_match.group(1))
                
                # トラック種別
                if 'ダ' in text:
                    result['track_type'] = 'ダート'
                elif '芝' in text:
                    result['track_type'] = '芝'
                
                # 馬場状態
                if '良' in text:
                    result['field_condition'] = '良'
                elif '稍' in text:
                    result['field_condition'] = '稍重'
                elif '重' in text:
                    result['field_condition'] = '重'
                elif '不' in text:
                    result['field_condition'] = '不良'
                
                # 天候
                if '晴' in text:
                    result['weather'] = '晴'
                elif '曇' in text:
                    result['weather'] = '曇'
                elif '雨' in text:
                    result['weather'] = '雨'
                elif '雪' in text:
                    result['weather'] = '雪'
        
        # レースクラス（レース名から推定）
        race_name = result.get('race_name', '')
        if 'G1' in race_name or 'GⅠ' in race_name:
            result['race_class'] = 'G1'
        elif 'G2' in race_name or 'GⅡ' in race_name:
            result['race_class'] = 'G2'
        elif 'G3' in race_name or 'GⅢ' in race_name:
            result['race_class'] = 'G3'
        elif '新馬' in race_name:
            result['race_class'] = '新馬'
        elif '未勝利' in race_name:
            result['race_class'] = '未勝利'
        elif 'オープン' in race_name or 'OP' in race_name:
            result['race_class'] = 'オープン'
        elif '1勝' in race_name:
            result['race_class'] = '1勝クラス'
        elif '2勝' in race_name:
            result['race_class'] = '2勝クラス'
        elif '3勝' in race_name:
            result['race_class'] = '3勝クラス'
        else:
            result['race_class'] = 'その他'
        
        # 結果テーブル
        result_table = soup.find('table', class_='ResultRefund')
        horses = []
        if result_table:
            rows = result_table.find_all('tr')[1:]  # ヘッダースキップ
            for row in rows:
                cols = row.find_all('td')
                if len(cols) >= 13:
                    horse = {
                        'chakujun': cols[0].text.strip(),
                        'wakuban': cols[1].text.strip(),
                        'umaban': cols[2].text.strip(),
                        'horse_name': cols[3].text.strip(),
                        'sex_age': cols[4].text.strip(),
                        'kinryo': cols[5].text.strip(),
                        'jockey': cols[6].text.strip(),
                        'time': cols[7].text.strip(),
                        'margin': cols[8].text.strip(),
                        'corner_positions': cols[10].text.strip() if len(cols) > 10 else '',
                        'last_3f': cols[11].text.strip() if len(cols) > 11 else '',
                        'odds': cols[12].text.strip() if len(cols) > 12 else '',
                    }
                    horses.append(horse)
        result['horses'] = horses
        
        # 払戻金
        payout_tables = soup.find_all('table', class_='Payout_Detail_Table')
        payouts = []
        for table in payout_tables:
            rows = table.find_all('tr')
            for row in rows:
                cols = row.find_all('td')
                if len(cols) >= 3:
                    payout = {
                        'type': cols[0].text.strip(),
                        'combination': cols[1].text.strip(),
                        'payout': cols[2].text.strip(),
                    }
                    payouts.append(payout)
        result['payouts'] = payouts
        
        print(f"✅ 完了: {result.get('race_name', 'Unknown')}")
        print(f"   馬数: {len(horses)}")
        print(f"   払戻: {len(payouts)} 種類")
        
        return result


def main():
    parser = argparse.ArgumentParser(
        description="完全版スクレイピング - Streamlit版と同じ構造"
    )
    parser.add_argument('--date', help='日付指定（YYYYMMDD）')
    parser.add_argument('--race', help='レースID指定（12桁）')
    parser.add_argument('--save', action='store_true', help='Supabaseに保存')
    parser.add_argument('--delay', type=float, default=2.0, help='リクエスト間隔（秒）')
    
    args = parser.parse_args()
    
    if not args.date and not args.race:
        parser.print_help()
        print("\n❌ --date または --race を指定してください")
        return
    
    print("━"*80)
    print("  🏇 完全版スクレイピング")
    print("━"*80)
    
    scraper = CompleteScraper()
    
    if args.date:
        # 日付指定
        race_ids = scraper.get_race_list(args.date)
        
        print(f"\n{'='*80}")
        print(f"📊 {len(race_ids)} レースのデータを取得します")
        print(f"{'='*80}")
        
        all_results = []
        for i, race_id in enumerate(race_ids, 1):
            print(f"\n[{i}/{len(race_ids)}] {race_id}")
            print("-"*80)
            
            result = scraper.scrape_race(race_id)
            all_results.append(result)
            
            # レート制限対策
            if i < len(race_ids):
                wait_time = args.delay + random.uniform(0, 1)
                print(f"⏳ {wait_time:.1f}秒待機...")
                time.sleep(wait_time)
        
        print(f"\n{'='*80}")
        print(f"✅ 完了: {len(all_results)} レース取得成功")
        print(f"{'='*80}")
        
        # TODO: Supabase保存
        if args.save:
            print("\n💾 Supabase保存は次のステップで実装")
    
    elif args.race:
        # レースID指定
        result = scraper.scrape_race(args.race)
        print(f"\n{'='*80}")
        print(f"✅ 完了")
        print(f"{'='*80}")
        print(f"レース名: {result.get('race_name')}")
        print(f"日付: {result.get('date')}")
        print(f"会場: {result.get('venue')}")
        print(f"距離: {result.get('distance')}m")
        print(f"馬場: {result.get('track_type')}")
        print(f"馬数: {len(result.get('horses', []))}")
        print(f"払戻: {len(result.get('payouts', []))}")


if __name__ == "__main__":
    main()
