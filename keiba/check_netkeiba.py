"""
Netkeibaのサイト構造を確認するスクリプト
実際にアクセスして、レスポンスを確認する
"""
import requests
from bs4 import BeautifulSoup
import time

def check_netkeiba_structure():
    """Netkeibaのサイト構造を確認"""
    
    # テストするURL
    test_urls = [
        "https://race.netkeiba.com",
        "https://race.netkeiba.com/top/",
        "https://race.netkeiba.com/top/race_list.html",
        "https://race.netkeiba.com/race/shutuba.html?race_id=202312230811",  # 2023年12月23日 東京11R（有馬記念）
        "https://db.netkeiba.com/race/202312230811",  # DBページ
    ]
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    
    session = requests.Session()
    
    for url in test_urls:
        print(f"\n{'='*80}")
        print(f"URL: {url}")
        print('='*80)
        
        try:
            resp = session.get(url, headers=headers, timeout=10, allow_redirects=True)
            
            print(f"ステータスコード: {resp.status_code}")
            print(f"最終URL: {resp.url}")
            print(f"Content-Type: {resp.headers.get('Content-Type', 'N/A')}")
            print(f"HTMLサイズ: {len(resp.text)} 文字")
            
            if resp.status_code == 200:
                # HTMLの構造を確認
                soup = BeautifulSoup(resp.text, 'html.parser')
                title = soup.find('title')
                print(f"タイトル: {title.text if title else 'なし'}")
                
                # テーブルの有無を確認
                tables = soup.find_all('table')
                print(f"テーブル数: {len(tables)}")
                
                # 最初の500文字を表示
                print("\nHTML先頭500文字:")
                print(resp.text[:500])
                
            elif resp.status_code == 400:
                print("❌ 400 Bad Request - レースIDが無効または存在しない")
            elif resp.status_code == 403:
                print("❌ 403 Forbidden - アクセスがブロックされている")
            elif resp.status_code == 404:
                print("❌ 404 Not Found - ページが見つからない")
            else:
                print(f"❌ その他のエラー: {resp.status_code}")
                
        except Exception as e:
            print(f"❌ エラー: {e}")
        
        # 丁寧に待つ
        time.sleep(2)
    
    print(f"\n{'='*80}")
    print("結論:")
    print("1. race.netkeiba.com は正常にアクセスできるか？")
    print("2. race_id パラメータは有効か？")
    print("3. 代わりに db.netkeiba.com を使うべきか？")
    print('='*80)

if __name__ == "__main__":
    check_netkeiba_structure()
