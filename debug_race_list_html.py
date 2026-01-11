"""
race_list.htmlの構造を確認
"""
import requests
from bs4 import BeautifulSoup

def debug_race_list_html():
    """race_list.htmlの内容を詳細に確認"""
    
    # テスト日付（2024年1月6日 - 土曜日、開催あり）
    date = "20240106"
    url = f"https://race.netkeiba.com/top/race_list.html?kaisai_date={date}"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    
    print(f"URL: {url}")
    print("=" * 80)
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        print(f"ステータスコード: {response.status_code}")
        print(f"Content-Length: {len(response.text)}")
        print("=" * 80)
        
        if response.status_code != 200:
            print(f"❌ エラー: HTTP {response.status_code}")
            return
        
        html = response.text
        soup = BeautifulSoup(html, 'html.parser')
        
        # 1. race_idを含むリンクを探す
        print("\n1. race_id を含むリンクを検索:")
        print("-" * 80)
        race_links = soup.find_all('a', href=lambda x: x and 'race_id' in x)
        print(f"見つかったリンク数: {len(race_links)}")
        
        if len(race_links) > 0:
            print("\n最初の5件:")
            for i, link in enumerate(race_links[:5]):
                href = link.get('href')
                text = link.get_text(strip=True)
                print(f"  {i+1}. href='{href}'")
                print(f"     text='{text}'")
        
        # 2. race_list 関連のクラスやIDを探す
        print("\n2. race_list 関連の要素を検索:")
        print("-" * 80)
        race_list_divs = soup.find_all(['div', 'ul', 'li'], class_=lambda x: x and 'race' in x.lower())
        print(f"race を含むclass の要素数: {len(race_list_divs)}")
        
        if len(race_list_divs) > 0:
            print("\n最初の3件:")
            for i, div in enumerate(race_list_divs[:3]):
                print(f"  {i+1}. <{div.name} class='{div.get('class')}'>")
        
        # 3. HTMLの一部を表示
        print("\n3. HTML冒頭 (最初の1000文字):")
        print("-" * 80)
        print(html[:1000])
        
        # 4. race_idという文字列を検索
        print("\n4. 'race_id' の出現回数:")
        print("-" * 80)
        count = html.count('race_id')
        print(f"'race_id': {count}回")
        
        if count > 0:
            # race_idの前後を表示
            idx = html.find('race_id')
            if idx != -1:
                print(f"\n最初の出現箇所 (前後100文字):")
                start = max(0, idx - 100)
                end = min(len(html), idx + 100)
                print(html[start:end])
        
        # 5. JavaScriptやデータ属性を探す
        print("\n5. data-* 属性を持つ要素:")
        print("-" * 80)
        data_elements = soup.find_all(attrs=lambda x: x and any(k.startswith('data-') for k in x.keys()))
        print(f"data-* 属性を持つ要素数: {len(data_elements)}")
        
        if len(data_elements) > 0:
            print("\n最初の5件:")
            for i, elem in enumerate(data_elements[:5]):
                data_attrs = {k: v for k, v in elem.attrs.items() if k.startswith('data-')}
                print(f"  {i+1}. <{elem.name}> {data_attrs}")
        
    except Exception as e:
        print(f"❌ エラー: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    debug_race_list_html()
