"""
netkeiba.com スクレイピング機能のテスト
"""
import requests
import time
from datetime import datetime

# テスト設定
BASE_URL = "http://localhost:3000"
TEST_YEAR = 2024
TEST_MONTH = 12

def test_calendar_api():
    """開催日取得APIのテスト"""
    print(f"\n{'='*60}")
    print(f"TEST 1: 開催日取得 ({TEST_YEAR}年{TEST_MONTH}月)")
    print(f"{'='*60}")
    
    url = f"{BASE_URL}/api/netkeiba/calendar?year={TEST_YEAR}&month={TEST_MONTH}"
    print(f"リクエスト: {url}")
    
    try:
        response = requests.get(url, timeout=30)
        print(f"ステータス: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            
            if 'error' in data:
                print(f"❌ エラー: {data['error']}")
                return None
            
            dates = data.get('dates', [])
            print(f"✅ 開催日数: {len(dates)}日")
            
            if dates:
                print(f"開催日: {', '.join(dates[:5])}{'...' if len(dates) > 5 else ''}")
                return dates
            else:
                print("⚠️ 開催日が見つかりませんでした")
                return []
        else:
            print(f"❌ HTTPエラー: {response.status_code}")
            print(f"レスポンス: {response.text[:200]}")
            return None
            
    except Exception as e:
        print(f"❌ エラー: {e}")
        return None

def test_race_list_api(date):
    """レース一覧取得APIのテスト"""
    print(f"\n{'='*60}")
    print(f"TEST 2: レース一覧取得 ({date})")
    print(f"{'='*60}")
    
    url = f"{BASE_URL}/api/netkeiba/calendar"
    print(f"リクエスト: POST {url}")
    
    try:
        response = requests.post(
            url,
            json={"date": date},
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        print(f"ステータス: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            
            if 'error' in data:
                print(f"❌ エラー: {data['error']}")
                return None
            
            races = data.get('races', [])
            print(f"✅ レース数: {len(races)}レース")
            
            if races:
                for i, race in enumerate(races[:3], 1):
                    print(f"  {i}. {race.get('venue', 'N/A')} {race.get('raceNumber', 'N/A')}R - {race.get('raceName', 'N/A')}")
                    print(f"     レースID: {race.get('raceId', 'N/A')}")
                if len(races) > 3:
                    print(f"  ... 他 {len(races) - 3} レース")
                return races
            else:
                print("⚠️ レースが見つかりませんでした")
                return []
        else:
            print(f"❌ HTTPエラー: {response.status_code}")
            print(f"レスポンス: {response.text[:200]}")
            return None
            
    except Exception as e:
        print(f"❌ エラー: {e}")
        return None

def test_race_scraping(race_id, user_id="test-user-id"):
    """レーススクレイピングAPIのテスト"""
    print(f"\n{'='*60}")
    print(f"TEST 3: レース詳細取得 (ID: {race_id})")
    print(f"{'='*60}")
    
    url = f"{BASE_URL}/api/netkeiba/race"
    print(f"リクエスト: POST {url}")
    
    try:
        response = requests.post(
            url,
            json={"raceId": race_id, "userId": user_id},
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        print(f"ステータス: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            
            if 'error' in data:
                print(f"❌ エラー: {data['error']}")
                return False
            
            if data.get('success'):
                print(f"✅ 取得成功")
                print(f"   出走馬数: {data.get('resultsCount', 0)}頭")
                print(f"   払戻情報: {data.get('payoutsCount', 0)}件")
                return True
            else:
                print(f"❌ 取得失敗")
                return False
        else:
            print(f"❌ HTTPエラー: {response.status_code}")
            print(f"レスポンス: {response.text[:500]}")
            return False
            
    except Exception as e:
        print(f"❌ エラー: {e}")
        return False

def main():
    print(f"\n{'#'*60}")
    print(f"# netkeiba.com スクレイピング機能テスト")
    print(f"# 開始時刻: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'#'*60}")
    
    # Next.jsサーバー接続確認
    print(f"\n[接続確認] Next.jsサーバー: {BASE_URL}")
    try:
        response = requests.get(BASE_URL, timeout=5)
        print(f"✅ サーバー稼働中 (HTTP {response.status_code})")
    except Exception as e:
        print(f"❌ サーバーに接続できません: {e}")
        print(f"\n⚠️ 以下のコマンドでNext.jsサーバーを起動してください:")
        print(f"   cd C:\\Users\\yuki2\\Documents\\ws\\keiba-ai-pro")
        print(f"   npm run dev")
        return
    
    # TEST 1: 開催日取得
    dates = test_calendar_api()
    
    if not dates:
        print(f"\n⚠️ 開催日が取得できないため、テストを中断します")
        print(f"\n【考えられる原因】")
        print(f"  1. netkeiba.comのHTML構造が変更された")
        print(f"  2. {TEST_YEAR}年{TEST_MONTH}月に開催がない")
        print(f"  3. アクセスがブロックされている")
        return
    
    # TEST 2: レース一覧取得（最初の開催日のみ）
    test_date = dates[0]
    races = test_race_list_api(test_date)
    
    if not races:
        print(f"\n⚠️ レース一覧が取得できないため、テストを中断します")
        print(f"\n【考えられる原因】")
        print(f"  1. netkeiba.comのHTML構造が変更された")
        print(f"  2. レースページへのアクセスが失敗")
        return
    
    # TEST 3: レーススクレイピング（最初のレースのみ）
    test_race = races[0]
    race_id = test_race.get('raceId')
    
    if race_id:
        print(f"\n⚠️ レート制限対策のため3秒待機...")
        time.sleep(3)
        
        success = test_race_scraping(race_id)
        
        if success:
            print(f"\n{'='*60}")
            print(f"✅ 全テスト成功！")
            print(f"{'='*60}")
            print(f"\n【次のステップ】")
            print(f"  Web UI (http://localhost:3000/data-collection) で")
            print(f"  期間指定一括取得を実行してください")
        else:
            print(f"\n{'='*60}")
            print(f"❌ レーススクレイピングに失敗")
            print(f"{'='*60}")
            print(f"\n【考えられる原因】")
            print(f"  1. Supabaseデータベース接続エラー")
            print(f"  2. netkeiba.comのHTML構造変更")
            print(f"  3. アクセス権限の問題")
    else:
        print(f"\n❌ レースIDが取得できませんでした")
    
    print(f"\n{'#'*60}")
    print(f"# 終了時刻: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'#'*60}\n")

if __name__ == '__main__':
    main()
