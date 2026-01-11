"""
netkeibaからのレース取得をテストするスクリプト
"""
from pathlib import Path
from datetime import datetime, timedelta
from keiba_ai.config import load_config
from keiba_ai.netkeiba.client import NetkeibaClient
from keiba_ai.netkeiba.parsers import extract_race_calendar

def test_fetch_race_calendar():
    print("=" * 80)
    print("netkeibaレース取得テスト")
    print("=" * 80)
    
    # 設定読み込み
    cfg = load_config("config.yaml")
    print(f"\n✅ 設定ファイル読み込み完了")
    print(f"   - base: {cfg.netkeiba.base}")
    print(f"   - sleep: {cfg.netkeiba.min_sleep_sec}〜{cfg.netkeiba.max_sleep_sec}秒")
    
    # クライアント作成
    client = NetkeibaClient(cfg.netkeiba, cfg.storage)
    print(f"\n✅ NetkeibaClient作成完了")
    
    # 今日から7日分のレースを取得
    print(f"\n📡 指定日のレース一覧を取得中...")
    today = datetime.now()
    all_results = {}
    
    for days in range(0, 7):
        test_date = today + timedelta(days=days)
        date_str = test_date.strftime("%Y%m%d")
        day_name = ['月','火','水','木','金','土','日'][test_date.weekday()]
        
        try:
            print(f"\n🔍 {test_date.strftime('%Y/%m/%d')}({day_name}) を取得中...")
            race_ids = client.fetch_race_list_by_date(date_str, use_cache=False)
            
            if race_ids:
                all_results[date_str] = race_ids
                print(f"   ✅ {len(race_ids)}レース取得")
                
                # 最初の3件を表示
                for i, race_id in enumerate(race_ids[:3]):
                    venue = race_id[8:10]
                    race_num = race_id[10:12]
                    print(f"      {i+1}. {race_id} (場:{venue}, R:{race_num})")
                if len(race_ids) > 3:
                    print(f"      ... 他{len(race_ids) - 3}件")
            else:
                print(f"   ⚪ 開催なし")
                
        except Exception as e:
            print(f"   ❌ エラー: {str(e)[:50]}")
    
    # サマリー
    print(f"\n" + "=" * 80)
    print(f"📊 取得結果サマリー")
    print(f"=" * 80)
    
    if all_results:
        total_races = sum(len(races) for races in all_results.values())
        print(f"✅ {len(all_results)}日分、合計{total_races}レースを取得")
        
        for date_str in sorted(all_results.keys()):
            race_count = len(all_results[date_str])
            year = date_str[:4]
            month = date_str[4:6]
            day = date_str[6:8]
            print(f"   - {year}年{month}月{day}日: {race_count}レース")
    else:
        print(f"⚠️ レースが取得できませんでした")
    
    return all_results

if __name__ == "__main__":
    results = test_fetch_race_calendar()
    
    print(f"\n" + "=" * 80)
    if results:
        total_races = sum(len(races) for races in results.values())
        print(f"✅ テスト完了: {len(results)}日分、合計{total_races}レースを取得")
    else:
        print(f"❌ テスト失敗: レースが取得できませんでした")
    print("=" * 80)
