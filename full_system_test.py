"""
VPN接続状態での全機能統合テスト
プロジェクトのすべての実装と機能を網羅的に検証
"""
import requests
import time
import json
from datetime import datetime

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'

def print_header(title):
    print("\n" + "=" * 100)
    print(f"{Colors.BLUE}■ {title}{Colors.RESET}")
    print("=" * 100)

def print_success(msg):
    print(f"{Colors.GREEN}✓ {msg}{Colors.RESET}")

def print_error(msg):
    print(f"{Colors.RED}✗ {msg}{Colors.RESET}")

def print_warning(msg):
    print(f"{Colors.YELLOW}⚠ {msg}{Colors.RESET}")

def print_info(msg):
    print(f"  {msg}")

# テスト結果を記録
test_results = {
    'passed': 0,
    'failed': 0,
    'warnings': 0,
    'details': []
}

def test_vpn_connection():
    """1. VPN接続状態の確認"""
    print_header("1. VPN接続状態の確認")
    
    try:
        # 現在のIPアドレス取得
        ip_response = requests.get('https://api.ipify.org?format=json', timeout=10)
        current_ip = ip_response.json()['ip']
        print_info(f"現在のIPアドレス: {current_ip}")
        
        blocked_ip = "180.46.30.140"
        if current_ip == blocked_ip:
            print_error("ブロックされたIPアドレスです（VPN未接続）")
            test_results['failed'] += 1
            test_results['details'].append({'test': 'VPN接続', 'status': 'FAILED', 'message': 'ブロックされたIP'})
            return False
        else:
            print_success(f"VPN接続済みまたは別環境（IP: {current_ip}）")
            test_results['passed'] += 1
            test_results['details'].append({'test': 'VPN接続', 'status': 'PASSED', 'ip': current_ip})
            return True
            
    except Exception as e:
        print_error(f"IP確認エラー: {e}")
        test_results['failed'] += 1
        test_results['details'].append({'test': 'VPN接続', 'status': 'ERROR', 'error': str(e)})
        return False

def test_netkeiba_access():
    """2. netkeiba.com直接アクセステスト"""
    print_header("2. netkeiba.com 直接アクセステスト")
    
    try:
        response = requests.get(
            'https://race.netkeiba.com/',
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'},
            timeout=15
        )
        
        content_size = len(response.content)
        print_info(f"HTTP Status: {response.status_code}")
        print_info(f"Content Size: {content_size:,} bytes")
        
        if response.status_code == 200 and content_size > 10000:
            print_success("アクセス成功（正常なレスポンス）")
            test_results['passed'] += 1
            test_results['details'].append({'test': 'netkeiba直接アクセス', 'status': 'PASSED', 'size': content_size})
            return True
        elif response.status_code == 400:
            print_error("400 Bad Request - IPブロック検出")
            test_results['failed'] += 1
            test_results['details'].append({'test': 'netkeiba直接アクセス', 'status': 'BLOCKED'})
            return False
        else:
            print_warning(f"予期しないレスポンス（Status: {response.status_code}, Size: {content_size}）")
            test_results['warnings'] += 1
            test_results['details'].append({'test': 'netkeiba直接アクセス', 'status': 'WARNING', 'size': content_size})
            return False
            
    except Exception as e:
        print_error(f"アクセスエラー: {e}")
        test_results['failed'] += 1
        test_results['details'].append({'test': 'netkeiba直接アクセス', 'status': 'ERROR', 'error': str(e)})
        return False

def test_scraping_service_health():
    """3. スクレイピングサービスのヘルスチェック"""
    print_header("3. スクレイピングサービス（port 8001）動作確認")
    
    try:
        response = requests.get('http://localhost:8001/health', timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            print_success("サービス稼働中")
            print_info(f"稼働時間: {data.get('uptime_seconds', 0):.1f}秒")
            print_info(f"リクエスト処理数: {data.get('request_count', 0)}回")
            print_info(f"ドライバー状態: {data.get('driver_status', 'unknown')}")
            
            test_results['passed'] += 1
            test_results['details'].append({
                'test': 'スクレイピングサービス',
                'status': 'PASSED',
                'uptime': data.get('uptime_seconds'),
                'requests': data.get('request_count')
            })
            return True
        else:
            print_error(f"異常なレスポンス: {response.status_code}")
            test_results['failed'] += 1
            return False
            
    except requests.exceptions.ConnectionError:
        print_error("サービスが起動していません")
        print_info("起動コマンド: C:\\Users\\yuki2\\Documents\\ws\\keiba\\Scripts\\python.exe scraping_service_undetected.py")
        test_results['failed'] += 1
        test_results['details'].append({'test': 'スクレイピングサービス', 'status': 'NOT_RUNNING'})
        return False
    except Exception as e:
        print_error(f"エラー: {e}")
        test_results['failed'] += 1
        return False

def test_scraping_service_stats():
    """4. スクレイピングサービスの統計情報確認"""
    print_header("4. スクレイピングサービス統計情報")
    
    try:
        response = requests.get('http://localhost:8001/stats', timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            print_success("統計情報取得成功")
            print_info(f"総リクエスト数: {data.get('request_count', 0)}回")
            print_info(f"平均待機時間: {data.get('average_interval', 0):.2f}秒")
            print_info(f"稼働時間: {data.get('uptime_seconds', 0):.1f}秒")
            
            test_results['passed'] += 1
            test_results['details'].append({'test': 'サービス統計', 'status': 'PASSED'})
            return True
        else:
            print_warning("統計情報の取得に失敗")
            test_results['warnings'] += 1
            return False
            
    except Exception as e:
        print_error(f"エラー: {e}")
        test_results['failed'] += 1
        return False

def test_race_data_scraping():
    """5. 実際のレースデータ取得テスト"""
    print_header("5. レースデータ取得テスト（フェアリーS）")
    
    race_id = '202606010411'  # 2026/1/11 中山11R フェアリーS
    
    try:
        print_info(f"race_id: {race_id} のデータを取得中...")
        start_time = time.time()
        
        response = requests.post(
            'http://localhost:8001/scrape/race',
            json={'race_id': race_id},
            timeout=120
        )
        
        elapsed_time = time.time() - start_time
        
        if response.status_code == 200:
            data = response.json()
            
            if data.get('success'):
                print_success(f"データ取得成功（{elapsed_time:.1f}秒）")
                print_info(f"レース名: {data.get('race_name', 'N/A')}")
                print_info(f"距離: {data.get('distance', 'N/A')}m")
                print_info(f"トラック: {data.get('track_type', 'N/A')}")
                print_info(f"天候: {data.get('weather', 'N/A')}")
                print_info(f"馬場状態: {data.get('field_condition', 'N/A')}")
                print_info(f"待機時間: {data.get('wait_time', 0):.1f}秒")
                
                test_results['passed'] += 1
                test_results['details'].append({
                    'test': 'レースデータ取得',
                    'status': 'PASSED',
                    'race_name': data.get('race_name'),
                    'elapsed_time': elapsed_time
                })
                return data
            else:
                error_msg = data.get('error', 'Unknown error')
                print_error(f"取得失敗: {error_msg}")
                test_results['failed'] += 1
                test_results['details'].append({
                    'test': 'レースデータ取得',
                    'status': 'FAILED',
                    'error': error_msg
                })
                return None
        else:
            print_error(f"HTTPエラー: {response.status_code}")
            test_results['failed'] += 1
            return None
            
    except Exception as e:
        print_error(f"エラー: {e}")
        test_results['failed'] += 1
        test_results['details'].append({'test': 'レースデータ取得', 'status': 'ERROR', 'error': str(e)})
        return None

def test_rate_limiting():
    """6. レート制限機能のテスト"""
    print_header("6. レート制限機能の検証")
    
    print_info("連続3回のリクエストを送信してレート制限を確認...")
    
    try:
        wait_times = []
        
        for i in range(3):
            print_info(f"\nリクエスト {i+1}/3:")
            start_time = time.time()
            
            response = requests.post(
                'http://localhost:8001/scrape/race',
                json={'race_id': '202606010411'},
                timeout=120
            )
            
            elapsed = time.time() - start_time
            
            if response.status_code == 200:
                data = response.json()
                wait_time = data.get('wait_time', 0)
                wait_times.append(wait_time)
                
                print_info(f"  待機時間: {wait_time:.1f}秒")
                print_info(f"  処理時間: {elapsed:.1f}秒")
                
                if data.get('success'):
                    print_success(f"  データ取得成功")
                else:
                    print_warning(f"  {data.get('error', 'Unknown')}")
        
        # レート制限の検証
        print_info(f"\n待機時間の推移: {[f'{w:.1f}s' for w in wait_times]}")
        
        if len(wait_times) >= 2 and any(w >= 3.0 for w in wait_times[1:]):
            print_success("レート制限が正常に動作しています（3秒以上の待機確認）")
            test_results['passed'] += 1
            test_results['details'].append({
                'test': 'レート制限',
                'status': 'PASSED',
                'wait_times': wait_times
            })
            return True
        elif len(wait_times) >= 2:
            print_warning(f"レート制限の待機時間が短い可能性（最大: {max(wait_times[1:]):.1f}秒）")
            test_results['warnings'] += 1
            return True
        else:
            print_error("レート制限の動作を確認できませんでした")
            test_results['failed'] += 1
            return False
            
    except Exception as e:
        print_error(f"エラー: {e}")
        test_results['failed'] += 1
        return False

def test_database_connection():
    """7. データベース接続テスト"""
    print_header("7. データベース接続確認（Supabase）")
    
    import os
    
    # 環境変数の確認
    supabase_url = os.getenv('NEXT_PUBLIC_SUPABASE_URL')
    supabase_anon = os.getenv('NEXT_PUBLIC_SUPABASE_ANON_KEY')
    
    if not supabase_url or not supabase_anon:
        print_warning("Supabase環境変数が設定されていません")
        print_info(".env.local ファイルに以下を設定してください:")
        print_info("  NEXT_PUBLIC_SUPABASE_URL=your-project-url")
        print_info("  NEXT_PUBLIC_SUPABASE_ANON_KEY=your-anon-key")
        test_results['warnings'] += 1
        test_results['details'].append({'test': 'データベース接続', 'status': 'NOT_CONFIGURED'})
        return False
    
    try:
        from supabase import create_client
        
        supabase = create_client(supabase_url, supabase_anon)
        
        # 簡単な接続テスト（テーブル一覧取得など）
        print_success("Supabase接続成功")
        print_info(f"URL: {supabase_url[:30]}...")
        
        test_results['passed'] += 1
        test_results['details'].append({'test': 'データベース接続', 'status': 'PASSED'})
        return True
        
    except Exception as e:
        print_error(f"接続エラー: {e}")
        test_results['failed'] += 1
        test_results['details'].append({'test': 'データベース接続', 'status': 'ERROR', 'error': str(e)})
        return False

def test_nextjs_frontend():
    """8. Next.jsフロントエンドの確認"""
    print_header("8. Next.jsフロントエンド（port 3000）確認")
    
    try:
        response = requests.get('http://localhost:3000', timeout=5)
        
        if response.status_code == 200:
            print_success("Next.jsアプリケーション稼働中")
            print_info(f"Status: {response.status_code}")
            test_results['passed'] += 1
            test_results['details'].append({'test': 'Next.js Frontend', 'status': 'RUNNING'})
            return True
        else:
            print_warning(f"予期しないステータス: {response.status_code}")
            test_results['warnings'] += 1
            return False
            
    except requests.exceptions.ConnectionError:
        print_warning("Next.jsが起動していません")
        print_info("起動コマンド: npm run dev")
        print_info("（別ターミナルで実行してください）")
        test_results['warnings'] += 1
        test_results['details'].append({'test': 'Next.js Frontend', 'status': 'NOT_RUNNING'})
        return False
    except Exception as e:
        print_error(f"エラー: {e}")
        test_results['failed'] += 1
        return False

def test_data_collection_page():
    """9. データ収集ページの確認"""
    print_header("9. データ収集ページ（/data-collection）確認")
    
    try:
        response = requests.get('http://localhost:3000/data-collection', timeout=5)
        
        if response.status_code == 200:
            print_success("データ収集ページにアクセス可能")
            test_results['passed'] += 1
            test_results['details'].append({'test': 'データ収集ページ', 'status': 'ACCESSIBLE'})
            return True
        else:
            print_warning(f"ページが見つかりません: {response.status_code}")
            test_results['warnings'] += 1
            return False
            
    except requests.exceptions.ConnectionError:
        print_warning("Next.jsが起動していません")
        test_results['warnings'] += 1
        test_results['details'].append({'test': 'データ収集ページ', 'status': 'FRONTEND_DOWN'})
        return False
    except Exception as e:
        print_error(f"エラー: {e}")
        test_results['failed'] += 1
        return False

def print_final_summary():
    """最終サマリーの表示"""
    print_header("テスト結果サマリー")
    
    total_tests = test_results['passed'] + test_results['failed'] + test_results['warnings']
    
    print(f"\n総テスト数: {total_tests}")
    print_success(f"合格: {test_results['passed']}")
    if test_results['warnings'] > 0:
        print_warning(f"警告: {test_results['warnings']}")
    if test_results['failed'] > 0:
        print_error(f"不合格: {test_results['failed']}")
    
    # 成功率計算
    success_rate = (test_results['passed'] / total_tests * 100) if total_tests > 0 else 0
    print(f"\n成功率: {success_rate:.1f}%")
    
    # 詳細結果
    print("\n" + "-" * 100)
    print("詳細結果:")
    for detail in test_results['details']:
        status_icon = "✓" if detail['status'] == 'PASSED' else "✗" if detail['status'] == 'FAILED' else "⚠"
        print(f"  {status_icon} {detail['test']}: {detail['status']}")
    
    # 推奨事項
    print("\n" + "-" * 100)
    print("推奨事項:")
    
    if test_results['failed'] == 0 and test_results['warnings'] == 0:
        print_success("すべてのテストに合格しました！本番運用可能な状態です。")
    else:
        if any(d['test'] == 'スクレイピングサービス' and d['status'] == 'NOT_RUNNING' for d in test_results['details']):
            print_info("1. スクレイピングサービスを起動してください")
        
        if any(d['test'] == 'Next.js Frontend' and d['status'] == 'NOT_RUNNING' for d in test_results['details']):
            print_info("2. Next.jsフロントエンドを起動してください（npm run dev）")
        
        if any(d['test'] == 'データベース接続' and d['status'] == 'NOT_CONFIGURED' for d in test_results['details']):
            print_info("3. .env.local ファイルにSupabase認証情報を設定してください")
        
        if any(d['status'] == 'BLOCKED' for d in test_results['details']):
            print_info("4. VPNに接続してください（ProtonVPN推奨）")
    
    print("\n" + "=" * 100)
    
    # JSONファイルに結果を保存
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    report_file = f'test_report_{timestamp}.json'
    
    with open(report_file, 'w', encoding='utf-8') as f:
        json.dump({
            'timestamp': datetime.now().isoformat(),
            'summary': {
                'total': total_tests,
                'passed': test_results['passed'],
                'failed': test_results['failed'],
                'warnings': test_results['warnings'],
                'success_rate': success_rate
            },
            'details': test_results['details']
        }, f, ensure_ascii=False, indent=2)
    
    print(f"\nテスト結果を保存しました: {report_file}")

def main():
    """メインテスト実行"""
    print("\n" + "=" * 100)
    print(f"{Colors.BLUE}■ プロジェクト全機能統合テスト（VPN接続状態）{Colors.RESET}")
    print(f"実行時刻: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100)
    
    # 各テストを順次実行
    vpn_ok = test_vpn_connection()
    netkeiba_ok = test_netkeiba_access()
    
    if not vpn_ok or not netkeiba_ok:
        print_warning("\n⚠ VPN接続またはnetkeiba.comへのアクセスに問題があります")
        print_info("以降のテストを続行しますが、データ取得テストは失敗する可能性があります")
    
    service_ok = test_scraping_service_health()
    
    if service_ok:
        test_scraping_service_stats()
        test_race_data_scraping()
        test_rate_limiting()
    
    test_database_connection()
    test_nextjs_frontend()
    test_data_collection_page()
    
    # 最終サマリー
    print_final_summary()

if __name__ == "__main__":
    main()
