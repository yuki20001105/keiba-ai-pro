"""
race_idがどこに埋め込まれているか詳細調査
"""
import requests
import re

def find_race_ids():
    """HTMLからrace_idを全パターンで抽出"""
    
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
        print(f"HTMLサイズ: {len(html)} bytes")
        print("=" * 80)
        
        # パターン1: race_id=数字
        print("\n【パターン1】race_id=数字 を検索:")
        print("-" * 80)
        pattern1 = re.findall(r'race_id[=:](\d+)', html)
        print(f"見つかった件数: {len(pattern1)}")
        if pattern1:
            print("最初の10件:")
            for i, race_id in enumerate(pattern1[:10]):
                print(f"  {i+1}. {race_id}")
        
        # パターン2: /race/数字/
        print("\n【パターン2】/race/数字/ を検索:")
        print("-" * 80)
        pattern2 = re.findall(r'/race/(\d{12,14})/', html)
        print(f"見つかった件数: {len(pattern2)}")
        if pattern2:
            print("最初の10件:")
            for i, race_id in enumerate(pattern2[:10]):
                print(f"  {i+1}. {race_id}")
        
        # パターン3: data-race-id="数字"
        print("\n【パターン3】data-race-id=\"数字\" を検索:")
        print("-" * 80)
        pattern3 = re.findall(r'data-race-id[=:]"?(\d+)"?', html)
        print(f"見つかった件数: {len(pattern3)}")
        if pattern3:
            print("最初の10件:")
            for i, race_id in enumerate(pattern3[:10]):
                print(f"  {i+1}. {race_id}")
        
        # パターン4: 202401060で始まる14桁の数字
        print("\n【パターン4】{date}で始まる14桁の数字:")
        print("-" * 80)
        pattern4 = re.findall(rf'{date}\d{{6}}', html)
        print(f"見つかった件数: {len(pattern4)}")
        if pattern4:
            # 重複除去
            unique_ids = list(set(pattern4))
            unique_ids.sort()
            print(f"ユニーク件数: {len(unique_ids)}")
            print("全件:")
            for i, race_id in enumerate(unique_ids):
                print(f"  {i+1}. {race_id}")
        
        # パターン5: "race_id"前後の文字列
        print("\n【パターン5】'race_id'の前後200文字 (全出現箇所):")
        print("-" * 80)
        positions = [m.start() for m in re.finditer(r'race_id', html)]
        print(f"'race_id'の出現位置: {len(positions)}箇所")
        
        for i, pos in enumerate(positions):
            start = max(0, pos - 100)
            end = min(len(html), pos + 200)
            print(f"\n--- 出現 {i+1}/{len(positions)} (位置: {pos}) ---")
            context = html[start:end]
            print(context)
            print("-" * 80)
        
    except Exception as e:
        print(f"❌ エラー: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    find_race_ids()
