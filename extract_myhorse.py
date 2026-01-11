"""
myhorse_パターンからrace_idを抽出
"""
import requests
import re

def extract_race_ids_from_myhorse():
    """id="myhorse_数字" からrace_idを抽出"""
    
    date = "20240106"
    url = f"https://race.netkeiba.com/top/race_list.html?kaisai_date={date}"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    
    print(f"URL: {url}")
    print("=" * 80)
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        html = response.text
        
        print(f"ステータスコード: {response.status_code}")
        print("=" * 80)
        
        # パターン: id="myhorse_数字"
        print("\n【方法1】id=\"myhorse_数字\" を検索:")
        print("-" * 80)
        pattern1 = re.findall(r'id="myhorse_(\d+)"', html)
        print(f"見つかった件数: {len(pattern1)}")
        if pattern1:
            print("全件:")
            for i, race_id in enumerate(pattern1):
                print(f"  {i+1}. {race_id} (長さ: {len(race_id)}桁)")
        
        # パターン: id='myhorse_数字'
        print("\n【方法2】id='myhorse_数字' を検索:")
        print("-" * 80)
        pattern2 = re.findall(r"id='myhorse_(\d+)'", html)
        print(f"見つかった件数: {len(pattern2)}")
        if pattern2:
            print("全件:")
            for i, race_id in enumerate(pattern2):
                print(f"  {i+1}. {race_id}")
        
        # パターン: すべてのmyhorse関連
        print("\n【方法3】'myhorse'を含む行を検索:")
        print("-" * 80)
        lines_with_myhorse = [line for line in html.split('\n') if 'myhorse' in line.lower()]
        print(f"見つかった行数: {len(lines_with_myhorse)}")
        if lines_with_myhorse:
            print("\n最初の5行:")
            for i, line in enumerate(lines_with_myhorse[:5]):
                print(f"  {i+1}. {line.strip()[:150]}")
        
        # 別のアプローチ: レース情報が含まれそうな構造を探す
        print("\n【方法4】RaceListやRaceDataを含む要素:")
        print("-" * 80)
        
        # <li class="Race"> や <div class="RaceList_DataItem">を探す
        race_item_pattern = re.findall(r'<li[^>]*class="[^"]*Race[^"]*"[^>]*>.*?</li>', html, re.DOTALL)
        print(f"<li class=\"Race\"> 要素: {len(race_item_pattern)}件")
        
        if len(race_item_pattern) > 0:
            print("\n最初の要素 (一部):")
            print(race_item_pattern[0][:500])
        
        # URLリンクを探す
        print("\n【方法5】/race/や/top/race.htmlへのリンク:")
        print("-" * 80)
        race_links = re.findall(r'href="([^"]*(?:race|Race)[^"]*)"', html)
        print(f"見つかったリンク数: {len(race_links)}")
        if race_links:
            # 重複除去
            unique_links = list(set(race_links))
            print(f"ユニーク件数: {len(unique_links)}")
            print("\n最初の10件:")
            for i, link in enumerate(unique_links[:10]):
                print(f"  {i+1}. {link}")
        
    except Exception as e:
        print(f"❌ エラー: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    extract_race_ids_from_myhorse()
