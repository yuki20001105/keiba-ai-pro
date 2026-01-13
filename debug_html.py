#!/usr/bin/env python3
"""
HTMLパーサーデバッグ - 実際のHTML構造を確認
"""
import requests
from bs4 import BeautifulSoup


def debug_html_structure(race_id: str):
    """HTMLの構造を詳細に調査"""
    url = f'https://race.netkeiba.com/race/result.html?race_id={race_id}'
    
    print(f"URL: {url}\n")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    
    response = requests.get(url, headers=headers, timeout=10)
    soup = BeautifulSoup(response.text, 'html.parser')
    
    print("="*80)
    print("【HTML構造調査】")
    print("="*80)
    
    # レース名のクラスをすべて探す
    print("\n1. レース名候補:")
    for tag in ['h1', 'h2', 'div']:
        candidates = soup.find_all(tag, class_=True)
        for c in candidates[:5]:  # 最初の5つだけ
            if c.get('class'):
                class_str = ' '.join(c.get('class', []))
                text = c.text.strip()[:50]
                if text:
                    print(f"   <{tag} class='{class_str}'>{text}...")
    
    # テーブルのクラスをすべて探す
    print("\n2. テーブルクラス:")
    tables = soup.find_all('table')
    for i, table in enumerate(tables[:5], 1):
        table_class = ' '.join(table.get('class', ['no-class']))
        rows = len(table.find_all('tr'))
        print(f"   テーブル{i}: class='{table_class}', rows={rows}")
    
    # divでRaceを含むクラスを探す
    print("\n3. 'Race'を含むdiv:")
    race_divs = soup.find_all('div', class_=lambda x: x and 'race' in str(x).lower())
    for div in race_divs[:10]:
        class_str = ' '.join(div.get('class', []))
        text = div.text.strip()[:50]
        print(f"   class='{class_str}': {text}")
    
    # メタデータ
    print("\n4. ページタイトル:")
    title = soup.find('title')
    if title:
        print(f"   {title.text.strip()}")
    
    # 実際のHTMLの一部を保存
    print("\n5. HTMLサンプル（最初の500文字）:")
    print(response.text[:500])
    
    return soup


if __name__ == "__main__":
    import sys
    race_id = sys.argv[1] if len(sys.argv) > 1 else "202406010101"
    debug_html_structure(race_id)
