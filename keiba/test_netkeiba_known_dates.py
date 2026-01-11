"""
既知の開催日でテスト（2024年のデータ）
"""
from pathlib import Path
from datetime import datetime
from keiba_ai.config import load_config
from keiba_ai.netkeiba.client import NetkeibaClient

def test_known_dates():
    print("=" * 80)
    print("既知の開催日でレース取得テスト")
    print("=" * 80)
    
    cfg = load_config("config.yaml")
    client = NetkeibaClient(cfg.netkeiba, cfg.storage)
    
    # 2024年の土日をテスト
    test_dates = [
        "20241228",  # 2024年12月28日（土）- 有馬記念の週
        "20241229",  # 2024年12月29日（日）- 有馬記念
        "20241221",  # 2024年12月21日（土）
        "20241222",  # 2024年12月22日（日）
    ]
    
    results = {}
    
    for date_str in test_dates:
        year = date_str[:4]
        month = date_str[4:6]
        day = date_str[6:8]
        
        print(f"\n🔍 {year}年{month}月{day}日 を取得中...")
        
        try:
            race_ids = client.fetch_race_list_by_date(date_str, use_cache=False)
            
            if race_ids:
                results[date_str] = race_ids
                print(f"   ✅ {len(race_ids)}レース取得")
                
                # 最初の5件を表示
                for i, race_id in enumerate(race_ids[:5]):
                    venue = race_id[8:10]
                    race_num = race_id[10:12]
                    print(f"      {i+1}. {race_id} (場:{venue}, R:{race_num})")
                if len(race_ids) > 5:
                    print(f"      ... 他{len(race_ids) - 5}件")
            else:
                print(f"   ⚪ 開催なし")
                
        except Exception as e:
            print(f"   ❌ エラー: {str(e)}")
            import traceback
            traceback.print_exc()
    
    # サマリー
    print(f"\n" + "=" * 80)
    if results:
        total_races = sum(len(races) for races in results.values())
        print(f"✅ {len(results)}日分、合計{total_races}レースを取得")
    else:
        print(f"❌ レースが取得できませんでした")
    print("=" * 80)
    
    return results

if __name__ == "__main__":
    test_known_dates()
