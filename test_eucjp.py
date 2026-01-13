#!/usr/bin/env python3
"""
EUC-JP対応スクレイピング - 文字化け修正版
"""
import requests
from bs4 import BeautifulSoup
import re


def scrape_race_eucjp(race_id: str) -> dict:
    """EUC-JPエンコーディングに対応したスクレイピング"""
    url = f'https://race.netkeiba.com/race/result.html?race_id={race_id}'
    
    print(f"URL: {url}")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    
    # 重要: EUC-JPでデコード
    response = requests.get(url, headers=headers, timeout=10)
    response.encoding = 'EUC-JP'  # これが重要！
    
    soup = BeautifulSoup(response.text, 'html.parser')
    
    result = {}
    
    # レース名
    race_name_tag = soup.find('h1', class_='RaceName')
    if race_name_tag:
        result['race_name'] = race_name_tag.text.strip()
        print(f"✅ レース名: {result['race_name']}")
    
    # レースデータ1（距離・天候など）
    race_data1 = soup.find('div', class_='RaceData01')
    if race_data1:
        spans = race_data1.find_all('span')
        for span in spans:
            text = span.text.strip()
            print(f"   データ: {text}")
    
    # 結果テーブル
    result_table = soup.find('table', class_='ResultRefund')
    if result_table:
        rows = result_table.find_all('tr')[1:]  # ヘッダーをスキップ
        print(f"\n✅ レース結果: {len(rows)} 頭")
        
        horses = []
        for row in rows:
            cols = row.find_all('td')
            if len(cols) >= 8:
                horse = {
                    'chakujun': cols[0].text.strip(),
                    'wakuban': cols[1].text.strip(),
                    'umaban': cols[2].text.strip(),
                    'horse_name': cols[3].text.strip(),
                    'sex_age': cols[4].text.strip(),
                    'kinryo': cols[5].text.strip(),
                    'jockey': cols[6].text.strip(),
                    'time': cols[7].text.strip(),
                }
                horses.append(horse)
                print(f"   {horse['chakujun']}着 {horse['umaban']}番 {horse['horse_name']}")
        
        result['horses'] = horses
    
    # 払戻金
    payout_tables = soup.find_all('table', class_='Payout_Detail_Table')
    if payout_tables:
        print(f"\n✅ 払戻金: {len(payout_tables)} テーブル")
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
                    print(f"   {payout['type']}: {payout['combination']} → {payout['payout']}")
        result['payouts'] = payouts
    
    return result


if __name__ == "__main__":
    import sys
    race_id = sys.argv[1] if len(sys.argv) > 1 else "202406010101"
    
    print("="*80)
    print("【EUC-JP対応スクレイピングテスト】")
    print("="*80 + "\n")
    
    result = scrape_race_eucjp(race_id)
    
    print("\n" + "="*80)
    print(f"【結果サマリー】")
    print("="*80)
    print(f"レース名: {result.get('race_name', 'Unknown')}")
    print(f"馬数: {len(result.get('horses', []))}")
    print(f"払戻種類: {len(result.get('payouts', []))}")
