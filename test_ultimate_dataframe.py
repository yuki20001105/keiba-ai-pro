"""
Ultimate版データを取得してDataFrame表示
"""
import requests
import pandas as pd
import json
from datetime import datetime

print("\n" + "="*80)
print("【Ultimate版データ取得テスト】")
print("="*80)

# Ultimate版スクレイパーにリクエスト
race_id = "202406010101"
print(f"\n→ レースID: {race_id} のデータ取得中...")
print("  (初回はChromeドライバー起動に時間がかかります)")

try:
    response = requests.post(
        "http://localhost:8001/scrape/ultimate",
        json={"race_id": race_id, "save_to_db": False},
        timeout=300
    )
    
    if response.status_code == 200:
        data = response.json()
        print("\n✓ データ取得成功！\n")
        
        # レース情報
        race_info = data.get('race_info', {})
        print(f"【レース基本情報】")
        print(f"  レース名: {race_info.get('race_name', 'N/A')}")
        print(f"  開催: {race_info.get('venue', 'N/A')} {race_info.get('day', 'N/A')}日目")
        print(f"  コース: {race_info.get('track_type', 'N/A')} {race_info.get('distance', 'N/A')}m")
        print(f"  天候: {race_info.get('weather', 'N/A')} / 馬場: {race_info.get('field_condition', 'N/A')}")
        print(f"  出走: {race_info.get('horse_count', 'N/A')}頭")
        
        # 派生特徴
        derived = data.get('derived_features', {})
        if derived:
            print(f"\n【派生特徴量（Ultimate版で追加）】")
            print(f"  market_entropy: {derived.get('market_entropy', 'N/A')}")
            print(f"  top3_probability: {derived.get('top3_probability', 'N/A')}")
        
        # 結果データをDataFrame化
        results = data.get('results', [])
        if results:
            df = pd.DataFrame(results)
            
            print(f"\n【データフレーム情報】")
            print(f"  行数: {len(df)} 頭")
            print(f"  列数: {len(df.columns)} 列")
            print(f"\n【全カラム一覧】")
            for i, col in enumerate(df.columns, 1):
                print(f"  {i:2d}. {col}")
            
            # 1着馬の詳細データ
            if 'finish_position' in df.columns:
                winner = df[df['finish_position'] == 1].iloc[0] if len(df[df['finish_position'] == 1]) > 0 else None
                
                if winner is not None:
                    print(f"\n【1着馬の詳細データ（Ultimate版特徴量）】")
                    print(f"\n--- 基本情報 ---")
                    print(f"  馬名: {winner.get('horse_name', 'N/A')}")
                    print(f"  horse_id: {winner.get('horse_id', 'N/A')} ⭐")
                    print(f"  性齢: {winner.get('sex_age', 'N/A')}")
                    print(f"  騎手: {winner.get('jockey_name', 'N/A')}")
                    print(f"  jockey_id: {winner.get('jockey_id', 'N/A')} ⭐")
                    print(f"  調教師: {winner.get('trainer_name', 'N/A')}")
                    print(f"  trainer_id: {winner.get('trainer_id', 'N/A')} ⭐")
                    
                    print(f"\n--- 結果 ---")
                    print(f"  タイム: {winner.get('finish_time', 'N/A')}")
                    print(f"  人気: {winner.get('popularity', 'N/A')}")
                    print(f"  オッズ: {winner.get('odds', 'N/A')}")
                    print(f"  上がり3F: {winner.get('last_3f', 'N/A')}")
                    print(f"  上がり順位: {winner.get('last_3f_rank', 'N/A')} ⭐")
                    
                    print(f"\n--- 馬体重 ---")
                    print(f"  weight_kg: {winner.get('weight_kg', 'N/A')} kg ⭐")
                    print(f"  weight_change: {winner.get('weight_change', 'N/A')} kg ⭐")
                    
                    print(f"\n--- 馬詳細（Ultimate版）---")
                    print(f"  毛色: {winner.get('horse_coat_color', 'N/A')} ⭐")
                    print(f"  セール価格: {winner.get('horse_sale_price', 'N/A')} ⭐")
                    print(f"  通算獲得賞金: {winner.get('horse_total_prize_money', 'N/A')} ⭐")
                    print(f"  通算出走: {winner.get('horse_total_runs', 'N/A')} 回 ⭐")
                    print(f"  通算勝利: {winner.get('horse_total_wins', 'N/A')} 勝 ⭐")
                    
                    print(f"\n--- 前走データ（Ultimate版）---")
                    print(f"  前走日付: {winner.get('prev_race_date', 'N/A')} ⭐")
                    print(f"  前走場所: {winner.get('prev_race_venue', 'N/A')} ⭐")
                    print(f"  前走距離: {winner.get('prev_race_distance', 'N/A')} m ⭐")
                    print(f"  前走着順: {winner.get('prev_race_finish', 'N/A')} 着 ⭐")
                    print(f"  前走馬体重: {winner.get('prev_race_weight', 'N/A')} kg ⭐")
                    print(f"  距離変化: {winner.get('distance_change', 'N/A')} m ⭐")
                    
                    print(f"\n--- 血統 ---")
                    print(f"  父: {winner.get('sire', 'N/A')}")
                    print(f"  母: {winner.get('dam', 'N/A')}")
                    print(f"  母父: {winner.get('damsire', 'N/A')}")
            
            # ラップタイムの確認
            lap_times = data.get('lap_times', {})
            lap_sectional = data.get('lap_times_sectional', {})
            
            if lap_times or lap_sectional:
                print(f"\n【ラップタイム（Ultimate版の特徴）】")
                if lap_times:
                    print(f"\n  累計ラップ:")
                    for dist, time in sorted(lap_times.items(), key=lambda x: int(x[0].replace('m', '')))[:6]:
                        print(f"    {dist}: {time}")
                
                if lap_sectional:
                    print(f"\n  区間ラップ（⭐Ultimate版のみ）:")
                    for dist, time in sorted(lap_sectional.items(), key=lambda x: int(x[0].replace('m', '')))[:6]:
                        print(f"    {dist}: {time}")
            
            # 上位3頭のサマリー
            print(f"\n【上位3頭のサマリー】")
            top3 = df.nsmallest(3, 'finish_position') if 'finish_position' in df.columns else df.head(3)
            
            summary_cols = ['finish_position', 'horse_name', 'horse_id', 'jockey_name', 
                          'odds', 'last_3f', 'weight_kg', 'weight_change']
            available_cols = [col for col in summary_cols if col in top3.columns]
            
            print(top3[available_cols].to_string(index=False))
            
            # DataFrameをCSV保存
            output_file = f"ultimate_data_{race_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            df.to_csv(output_file, index=False, encoding='utf-8-sig')
            print(f"\n✓ データをCSV保存: {output_file}")
            print(f"  列数: {len(df.columns)} 列（Ultimate版）")
            
        else:
            print("\n✗ 結果データがありません")
            
    else:
        print(f"\n✗ HTTPエラー: {response.status_code}")
        print(f"  {response.text}")
        
except requests.exceptions.Timeout:
    print("\n✗ タイムアウト: データ取得に時間がかかりすぎています")
    print("  ※初回はChromeドライバーのダウンロードに時間がかかります")
except Exception as e:
    print(f"\n✗ エラー: {e}")

print("\n" + "="*80)
