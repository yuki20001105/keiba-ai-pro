"""
プロキシサーバー経由でnetkeiba.comにアクセステスト
IPブロックを回避するための方法
"""
import requests
from requests.exceptions import ProxyError, ConnectTimeout, RequestException
import time

# 無料プロキシリスト（テスト用）
# 注意: 無料プロキシは不安定で遅いことが多い
FREE_PROXIES = [
    # 日本のプロキシ（優先）
    {"http": "http://153.120.140.135:3128", "https": "http://153.120.140.135:3128"},
    {"http": "http://160.16.226.31:3128", "https": "http://160.16.226.31:3128"},
    
    # アジアのプロキシ
    {"http": "http://103.152.112.162:80", "https": "http://103.152.112.162:80"},
    {"http": "http://43.134.68.153:3128", "https": "http://43.134.68.153:3128"},
]

def test_proxy(proxy, timeout=10):
    """プロキシが動作するかテスト"""
    try:
        response = requests.get(
            "http://httpbin.org/ip",
            proxies=proxy,
            timeout=timeout
        )
        if response.status_code == 200:
            return True, response.json().get('origin', 'Unknown IP')
        return False, None
    except Exception as e:
        return False, str(e)[:50]

def test_netkeiba_with_proxy(proxy, proxy_name):
    """プロキシ経由でnetkeiba.comにアクセス"""
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1'
    }
    
    test_urls = [
        ("https://race.netkeiba.com/", "トップページ"),
        ("https://race.netkeiba.com/race/shutuba.html?race_id=202606010411", "出馬表"),
    ]
    
    print(f"\n{'='*80}")
    print(f"プロキシテスト: {proxy_name}")
    print(f"{'='*80}")
    
    # まずプロキシ自体をテスト
    print("プロキシ接続テスト中...")
    is_working, result = test_proxy(proxy, timeout=10)
    
    if not is_working:
        print(f"✗ プロキシが使用できません: {result}")
        return False
    
    print(f"✓ プロキシ接続成功 - IP: {result}")
    
    # netkeiba.comにアクセス
    for url, description in test_urls:
        print(f"\n[{description}]")
        print(f"URL: {url}")
        
        try:
            response = requests.get(
                url,
                headers=headers,
                proxies=proxy,
                timeout=15,
                allow_redirects=True
            )
            
            status = response.status_code
            content_length = len(response.content)
            
            print(f"✓ Status: {status}")
            print(f"  Content-Length: {content_length:,} bytes")
            
            if status == 200:
                # HTMLの内容をチェック
                content = response.text
                
                checks = [
                    ('RaceName', 'レース名'),
                    ('race_id', 'race_id'),
                    ('<table', 'テーブル'),
                    ('netkeiba', 'netkeiba'),
                ]
                
                print("  HTML要素チェック:")
                found = False
                for keyword, label in checks:
                    if keyword in content:
                        print(f"    ✓ {label} が見つかりました")
                        found = True
                
                if found:
                    print(f"\n  🎉 成功！このプロキシでnetkeiba.comにアクセスできました")
                    print(f"  プロキシ設定: {proxy}")
                    return True
                else:
                    print("    ⚠ 主要要素が見つかりません")
            
            elif status == 400:
                print(f"  ✗ 400 Bad Request - このプロキシもブロックされています")
            elif status == 403:
                print(f"  ✗ 403 Forbidden - アクセス拒否")
            else:
                print(f"  ⚠ 予期しないステータスコード: {status}")
                
        except ProxyError as e:
            print(f"  ✗ プロキシエラー: {str(e)[:100]}")
        except ConnectTimeout:
            print(f"  ✗ タイムアウト（プロキシが遅すぎる）")
        except RequestException as e:
            print(f"  ✗ リクエストエラー: {type(e).__name__}: {str(e)[:100]}")
        except Exception as e:
            print(f"  ✗ エラー: {type(e).__name__}: {str(e)[:100]}")
    
    return False

def main():
    """メイン処理"""
    
    print("=" * 80)
    print("プロキシ経由でnetkeiba.comアクセステスト")
    print("=" * 80)
    print("\n注意: 無料プロキシは不安定で、ほとんどが動作しないことがあります")
    print("推奨: 有料プロキシサービス（Bright Data, Oxylabs, SmartProxy等）の使用")
    print()
    
    # プロキシなしで試す（現在の状態確認）
    print("\n[プロキシなしでテスト - 現在の状態確認]")
    try:
        response = requests.get(
            "https://race.netkeiba.com/",
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=10
        )
        print(f"Status: {response.status_code} - プロキシなしでは {response.status_code} エラー")
    except Exception as e:
        print(f"エラー: {type(e).__name__}")
    
    # 各プロキシを試す
    success = False
    for i, proxy in enumerate(FREE_PROXIES, 1):
        proxy_name = f"プロキシ #{i}"
        
        if test_netkeiba_with_proxy(proxy, proxy_name):
            success = True
            print("\n" + "="*80)
            print("✓ アクセス可能なプロキシが見つかりました！")
            print(f"使用するプロキシ: {proxy}")
            print("="*80)
            break
        
        # 次のプロキシを試す前に少し待つ
        if i < len(FREE_PROXIES):
            print("\n次のプロキシを試します...")
            time.sleep(2)
    
    if not success:
        print("\n" + "="*80)
        print("✗ 利用可能なプロキシが見つかりませんでした")
        print("="*80)
        print("\n推奨される解決策:")
        print("1. 有料プロキシサービスの使用")
        print("   - Bright Data: https://brightdata.com/")
        print("   - Oxylabs: https://oxylabs.io/")
        print("   - SmartProxy: https://smartproxy.com/")
        print("2. VPNの使用（NordVPN, ExpressVPNなど）")
        print("3. 時間を置く（数時間〜24時間待つ）")
        print("4. 別のネットワーク（スマホのテザリングなど）から試す")
        print("5. クラウドサーバー（AWS, GCP, Azure）から実行")

if __name__ == "__main__":
    main()
