"""
一括取得の時間計測テスト
"""
import requests
import time
from datetime import datetime

BASE_URL = "http://localhost:3000"

def test_single_race_timing():
    """1レースの取得時間を計測"""
    print("\n" + "="*60)
    print("TEST: 1レースの取得時間計測")
    print("="*60)
    
    # 実在するレースID（2024年12月1日 中山 5回1日 1R）
    race_id = "202412010601011"
    user_id = "test-user-id"
    
    start_time = time.time()
    
    try:
        response = requests.post(
            f"{BASE_URL}/api/netkeiba/race",
            json={"raceId": race_id, "userId": user_id},
            headers={"Content-Type": "application/json"},
            timeout=60
        )
        
        elapsed = time.time() - start_time
        
        print(f"ステータス: {response.status_code}")
        print(f"所要時間: {elapsed:.2f}秒")
        
        if response.status_code == 200:
            data = response.json()
            if data.get('success'):
                print(f"✅ 取得成功")
                print(f"   出走馬数: {data.get('resultsCount', 0)}頭")
            else:
                print(f"❌ 取得失敗: {data.get('error', 'Unknown')}")
        
        return elapsed
        
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"❌ エラー: {e}")
        print(f"所要時間: {elapsed:.2f}秒")
        return elapsed

def test_nonexistent_race_timing():
    """存在しないレースの取得時間を計測"""
    print("\n" + "="*60)
    print("TEST: 存在しないレースの取得時間計測")
    print("="*60)
    
    # 存在しないレースID
    race_id = "202412019999991"
    user_id = "test-user-id"
    
    start_time = time.time()
    
    try:
        response = requests.post(
            f"{BASE_URL}/api/netkeiba/race",
            json={"raceId": race_id, "userId": user_id},
            headers={"Content-Type": "application/json"},
            timeout=60
        )
        
        elapsed = time.time() - start_time
        
        print(f"ステータス: {response.status_code}")
        print(f"所要時間: {elapsed:.2f}秒")
        
        if response.status_code == 200:
            data = response.json()
            print(f"レスポンス: success={data.get('success')}")
        
        return elapsed
        
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"❌ エラー: {e}")
        print(f"所要時間: {elapsed:.2f}秒")
        return elapsed

def test_current_logic_simulation():
    """現在のロジックのシミュレーション"""
    print("\n" + "="*60)
    print("TEST: 現在のロジックの推定所要時間")
    print("="*60)
    
    # 1日あたりの試行回数
    venues = 10  # 競馬場
    kai = 5      # 開催回
    nichi = 12   # 開催日
    races = 12   # レース番号
    
    total_attempts = venues * kai * nichi * races
    print(f"1日あたりの試行回数: {venues} × {kai} × {nichi} × {races} = {total_attempts}回")
    
    # 各リクエストの平均時間
    success_time = 5.0  # 成功時（スクレイピング + 3秒待機）
    failure_time = 3.0  # 失敗時（HTTP取得のみ）
    
    # 実際の開催日は1日あたり通常2-3競馬場、各12レース程度
    actual_races_per_day = 36  # 3競馬場 × 12レース
    failed_attempts = total_attempts - actual_races_per_day
    
    estimated_time = (actual_races_per_day * success_time) + (failed_attempts * failure_time)
    
    print(f"\n推定所要時間（1日あたり）:")
    print(f"  成功: {actual_races_per_day}回 × {success_time}秒 = {actual_races_per_day * success_time}秒")
    print(f"  失敗: {failed_attempts}回 × {failure_time}秒 = {failed_attempts * failure_time}秒")
    print(f"  合計: {estimated_time}秒 = {estimated_time / 60:.1f}分 = {estimated_time / 3600:.1f}時間")
    
    # 1ヶ月あたり（開催日5日として）
    days_per_month = 5
    monthly_time = estimated_time * days_per_month
    print(f"\n1ヶ月あたり（開催日{days_per_month}日）:")
    print(f"  合計: {monthly_time}秒 = {monthly_time / 60:.1f}分 = {monthly_time / 3600:.1f}時間")
    
    print(f"\n⚠️ 問題点:")
    print(f"  - 存在しないレースに対しても毎回HTTPリクエストを送信")
    print(f"  - 7200回中36回しか成功しない（99.5%が無駄なリクエスト）")
    print(f"  - 1日のデータ取得に{estimated_time / 60:.1f}分かかる")

def main():
    print(f"\n{'#'*60}")
    print(f"# 一括取得の時間計測テスト")
    print(f"# 開始時刻: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'#'*60}")
    
    # サーバー接続確認
    print(f"\n[接続確認] Next.jsサーバー: {BASE_URL}")
    try:
        response = requests.get(BASE_URL, timeout=5)
        print(f"✅ サーバー稼働中")
    except Exception as e:
        print(f"❌ サーバーに接続できません: {e}")
        return
    
    # テスト実行
    success_time = test_single_race_timing()
    failure_time = test_nonexistent_race_timing()
    
    print(f"\n{'='*60}")
    print(f"計測結果:")
    print(f"  成功時: {success_time:.2f}秒")
    print(f"  失敗時: {failure_time:.2f}秒")
    print(f"{'='*60}")
    
    # シミュレーション
    test_current_logic_simulation()
    
    print(f"\n{'='*60}")
    print(f"推奨される改善策:")
    print(f"{'='*60}")
    print(f"1. 開催情報APIを使用して、実際に開催された競馬場のみを対象にする")
    print(f"2. レースIDの生成ロジックを改善（開催回・開催日の範囲を制限）")
    print(f"3. 存在しないレースは即座にスキップ（レート制限なし）")
    print(f"4. バッチ処理: 1日分まとめて取得してからDBに保存")
    
    print(f"\n{'#'*60}")
    print(f"# 終了時刻: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'#'*60}\n")

if __name__ == '__main__':
    main()
