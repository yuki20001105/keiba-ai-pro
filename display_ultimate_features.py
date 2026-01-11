"""
Ultimate版の主要特徴量を見やすく表示
"""
import requests
import pandas as pd
from datetime import datetime

print("=" * 80)
print("【Ultimate版 主要特徴量の確認】")
print("=" * 80)

race_id = "202406010101"
print(f"\n→ レースID: {race_id} のデータ取得中...")

try:
    response = requests.post(
        "http://localhost:8001/scrape/ultimate",
        json={"race_id": race_id, "include_details": True},
        timeout=300
    )
    
    if response.status_code != 200:
        print(f"✗ エラー: {response.status_code}")
        exit(1)
    
    data = response.json()
    print("✓ データ取得成功！")
    
    # レース情報
    race_info = data.get('race_info', {})
    print(f"\n【レース基本情報】")
    print(f"  レース名: {race_info.get('race_name')}")
    print(f"  開催: {race_info.get('venue')} {race_info.get('day')}日目")
    print(f"  コース: {race_info.get('track_type')} {race_info.get('distance')}")
    print(f"  天候: {race_info.get('weather')} / 馬場: {race_info.get('track_condition')}")
    print(f"  出走: {race_info.get('num_horses')}頭")
    
    # 派生特徴量（Ultimate版で追加された分析指標）
    derived = data.get('derived_features', {})
    print(f"\n【派生特徴量（⭐Ultimate版）】")
    print(f"  market_entropy: {derived.get('market_entropy', 'N/A')}")
    print(f"  top3_probability: {derived.get('top3_probability', 'N/A')}")
    
    # ラップタイム
    lap_times = data.get('lap_times', {})
    lap_sectional = data.get('lap_times_sectional', {})
    
    if lap_times:
        print(f"\n【ラップタイム: 累計】")
        sorted_laps = sorted(lap_times.items(), key=lambda x: int(x[0].replace('m', '')))
        for dist, time in sorted_laps[:6]:
            print(f"  {dist}: {time}")
    
    if lap_sectional:
        print(f"\n【ラップタイム: 区間（⭐Ultimate版のみ）】")
        sorted_sects = sorted(lap_sectional.items(), key=lambda x: int(x[0].replace('m', '')))
        for dist, time in sorted_sects[:6]:
            print(f"  {dist}: {time}")
    
    # 結果データを展開
    results = data.get('results', [])
    print(f"\n【出走馬数】 {len(results)}頭")
    
    # 1着馬の詳細を表示
    winner = None
    for r in results:
        try:
            if int(r.get('finish_position', 999)) == 1:
                winner = r
                break
        except:
            pass
    
    if not winner:
        print("✗ 1着馬が見つかりません")
        exit(1)
    
    print(f"\n{'='*80}")
    print("【1着馬の詳細 - Ultimate版特徴量】")
    print('='*80)
    
    print(f"\n--- 基本情報 ---")
    print(f"  馬名: {winner.get('horse_name')}")
    print(f"  horse_id: {winner.get('horse_id')} ⭐")
    print(f"  性齢: {winner.get('sex_age')}")
    print(f"  騎手: {winner.get('jockey_name')}")
    print(f"  jockey_id: {winner.get('jockey_id')} ⭐")
    print(f"  調教師: {winner.get('trainer_name')}")
    print(f"  trainer_id: {winner.get('trainer_id')} ⭐")
    
    print(f"\n--- 結果 ---")
    print(f"  着順: {winner.get('finish_position')}着")
    print(f"  タイム: {winner.get('finish_time')}")
    print(f"  人気: {winner.get('popularity')}番人気")
    print(f"  オッズ: {winner.get('odds')}倍")
    print(f"  上がり3F: {winner.get('last_3f')}")
    print(f"  上がり順位: {winner.get('last_3f_rank')} ⭐")
    
    print(f"\n--- 馬体重（⭐分解済み） ---")
    print(f"  元の表記: {winner.get('weight')}")
    print(f"  weight_kg: {winner.get('weight_kg')} kg ⭐")
    print(f"  weight_change: {winner.get('weight_change')} kg ⭐")
    
    # 馬詳細
    horse_details = winner.get('horse_details', {})
    if horse_details:
        print(f"\n--- 馬の詳細情報（⭐Ultimate版）---")
        print(f"  生年月日: {horse_details.get('birth_date', 'N/A')}")
        print(f"  毛色: {horse_details.get('coat_color', 'N/A')} ⭐")
        print(f"  調教師: {horse_details.get('trainer', 'N/A')}")
        print(f"  馬主: {horse_details.get('owner', 'N/A')}")
        print(f"  生産者: {horse_details.get('breeder', 'N/A')}")
        print(f"  産地: {horse_details.get('birthplace', 'N/A')}")
        print(f"  セール価格: {horse_details.get('sale_price', 'N/A')} ⭐")
        print(f"  通算獲得賞金: {horse_details.get('total_prize_money', 'N/A')} ⭐")
        print(f"  通算出走: {horse_details.get('total_runs', 'N/A')} 回 ⭐")
        print(f"  通算勝利: {horse_details.get('total_wins', 'N/A')} 勝 ⭐")
        
        # 血統情報
        pedigree = horse_details.get('pedigree', {})
        if pedigree:
            print(f"\n  【血統】")
            print(f"    父: {pedigree.get('sire', 'N/A')}")
            print(f"    母: {pedigree.get('dam', 'N/A')}")
            print(f"    母父: {pedigree.get('damsire', 'N/A')}")
        
        # 過去成績（最近3走）
        past_perfs = horse_details.get('past_performances', [])
        if past_perfs:
            print(f"\n  【過去成績（⭐Ultimate版で前走データ抽出）】")
            for i, perf in enumerate(past_perfs[:3], 1):
                print(f"    {i}走前:")
                print(f"      日付: {perf.get('date', 'N/A')} ⭐")
                print(f"      場所: {perf.get('venue', 'N/A')} ⭐")
                print(f"      レース名: {perf.get('race_name', 'N/A')}")
                print(f"      距離: {perf.get('distance', 'N/A')} ⭐")
                print(f"      着順: {perf.get('finish', 'N/A')} ⭐")
                print(f"      騎手: {perf.get('jockey', 'N/A')}")
                print(f"      馬体重: {perf.get('weight', 'N/A')} ⭐")
    
    # 騎手詳細
    jockey_details = winner.get('jockey_details', {})
    if jockey_details:
        print(f"\n--- 騎手統計（⭐Ultimate版）---")
        print(f"  jockey_id: {jockey_details.get('jockey_id', 'N/A')} ⭐")
        print(f"  勝率: {jockey_details.get('win_rate', 'N/A')}%")
        print(f"  連対率: {jockey_details.get('place_rate_top2', 'N/A')}%")
        print(f"  複勝率: {jockey_details.get('show_rate', 'N/A')}%")
    
    # 調教師詳細
    trainer_details = winner.get('trainer_details', {})
    if trainer_details:
        print(f"\n--- 調教師統計（⭐Ultimate版）---")
        print(f"  trainer_id: {trainer_details.get('trainer_id', 'N/A')} ⭐")
        print(f"  勝率: {trainer_details.get('win_rate', 'N/A')}%")
        print(f"  連対率: {trainer_details.get('place_rate_top2', 'N/A')}%")
    
    # コーナー通過順位
    corner_positions = data.get('corner_positions', {})
    if corner_positions:
        print(f"\n【コーナー通過順位】")
        for corner, positions in sorted(corner_positions.items()):
            print(f"  {corner}: {positions}")
    
    # Ultimate版の特徴量カウント
    print(f"\n{'='*80}")
    print("【Ultimate版の特徴量カウント】")
    print('='*80)
    
    feature_count = {
        'レース基本情報': 16,
        '結果テーブル（ID含む）': 20,
        '馬詳細（毛色・セール価格等）': 14,
        '過去成績（前走データ）': 6,
        '騎手統計': 4,
        '調教師統計': 3,
        'ラップ累計': 12,
        'ラップ区間（⭐新規）': 12,
        'コーナー': 4,
        '血統': 3,
    }
    
    total = 0
    for category, count in feature_count.items():
        print(f"  {category}: {count}列")
        total += count
    
    print(f"\n  【合計】 {total}列（標準版60列 → Ultimate版90+列）")
    
    print(f"\n{'='*80}")
    print("✓ Ultimate版の主要特徴量の確認が完了しました")
    print('='*80)
    
except requests.exceptions.Timeout:
    print("✗ タイムアウト: データ取得に5分以上かかりました")
except Exception as e:
    print(f"✗ エラー: {e}")
    import traceback
    traceback.print_exc()
