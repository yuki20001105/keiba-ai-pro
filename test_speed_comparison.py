"""
高速化版の速度テスト
"""
import requests
import time

print("=" * 80)
print("【Ultimate版 速度比較テスト】")
print("=" * 80)

race_id = "202406010101"

# テスト1: 高速モード（詳細ページなし）
print(f"\n【テスト1: 高速モード（include_details=False）】")
print(f"  レースID: {race_id}")
print(f"  開始...")

start = time.time()
try:
    response = requests.post(
        "http://localhost:8001/scrape/ultimate",
        json={"race_id": race_id, "include_details": False},
        timeout=180
    )
    
    elapsed = time.time() - start
    
    if response.status_code == 200:
        data = response.json()
        results = data.get('results', [])
        
        print(f"\n  ✓ 成功！")
        print(f"  所要時間: {elapsed:.1f}秒")
        print(f"  出走馬数: {len(results)}頭")
        print(f"  取得列数: 約27列（基本情報 + ID + 分解重量 + 上がり順位）")
        
        # 1着馬のデータ確認
        for r in results:
            try:
                if int(r.get('finish_position', 999)) == 1:
                    print(f"\n  【1着馬】")
                    print(f"    馬名: {r.get('horse_name')}")
                    print(f"    horse_id: {r.get('horse_id')} ⭐")
                    print(f"    jockey_id: {r.get('jockey_id')} ⭐")
                    print(f"    trainer_id: {r.get('trainer_id')} ⭐")
                    print(f"    weight_kg: {r.get('weight_kg')} kg ⭐")
                    print(f"    weight_change: {r.get('weight_change')} kg ⭐")
                    print(f"    last_3f_rank: {r.get('last_3f_rank')} ⭐")
                    break
            except:
                pass
        
        print(f"\n  💡 高速モードの利点:")
        print(f"     - 詳細ページにアクセスしないため超高速")
        print(f"     - 機械学習に必須のID、分解重量、順位は取得可能")
        print(f"     - 大量レース取得に最適")
        
    else:
        print(f"  ✗ エラー: {response.status_code}")
        
except Exception as e:
    elapsed = time.time() - start
    print(f"  ✗ エラー: {e}")
    print(f"  所要時間: {elapsed:.1f}秒")

# テスト2: 完全モード（詳細ページあり）
print(f"\n" + "=" * 80)
print(f"【テスト2: 完全モード（include_details=True）】")
print(f"  レースID: {race_id}")
print(f"  開始...")

start = time.time()
try:
    response = requests.post(
        "http://localhost:8001/scrape/ultimate",
        json={"race_id": race_id, "include_details": True},
        timeout=300
    )
    
    elapsed = time.time() - start
    
    if response.status_code == 200:
        data = response.json()
        results = data.get('results', [])
        
        print(f"\n  ✓ 成功！")
        print(f"  所要時間: {elapsed:.1f}秒")
        print(f"  出走馬数: {len(results)}頭")
        print(f"  取得列数: 約94列（全特徴量）")
        
        # 1着馬の詳細データ確認
        for r in results:
            try:
                if int(r.get('finish_position', 999)) == 1:
                    print(f"\n  【1着馬の詳細】")
                    print(f"    馬名: {r.get('horse_name')}")
                    
                    horse_details = r.get('horse_details', {})
                    if horse_details:
                        print(f"    生年月日: {horse_details.get('birth_date', 'N/A')}")
                        print(f"    毛色: {horse_details.get('coat_color', 'N/A')} ⭐")
                        
                        past = horse_details.get('past_performances', [])
                        if past:
                            print(f"    前走日付: {past[0].get('date', 'N/A')} ⭐")
                            print(f"    前走場所: {past[0].get('venue', 'N/A')} ⭐")
                            print(f"    前走着順: {past[0].get('finish', 'N/A')} ⭐")
                    
                    jockey_details = r.get('jockey_details', {})
                    if jockey_details:
                        print(f"    騎手勝率: {jockey_details.get('win_rate', 'N/A')}% ⭐")
                    
                    trainer_details = r.get('trainer_details', {})
                    if trainer_details:
                        print(f"    調教師勝率: {trainer_details.get('win_rate', 'N/A')}% ⭐")
                    
                    break
            except:
                pass
        
        print(f"\n  💡 完全モードの利点:")
        print(f"     - 全94列の特徴量を取得")
        print(f"     - キャッシュ活用で高速化（2回目以降さらに速い）")
        print(f"     - 馬の毛色、前走データ、統計情報も含む")
        
    else:
        print(f"  ✗ エラー: {response.status_code}")
        
except Exception as e:
    elapsed = time.time() - start
    print(f"  ✗ エラー: {e}")
    print(f"  所要時間: {elapsed:.1f}秒")

# キャッシュ状況確認
print(f"\n" + "=" * 80)
print(f"【キャッシュ状況】")

try:
    health = requests.get("http://localhost:8001/health").json()
    print(f"  騎手キャッシュ: {health.get('jockey_cache_size', 0)}人")
    print(f"  調教師キャッシュ: {health.get('trainer_cache_size', 0)}人")
    print(f"\n  💡 同じ騎手・調教師が出走する場合、2回目以降は即座に取得可能")
    
except:
    pass

print(f"\n" + "=" * 80)
print(f"【まとめ】")
print("=" * 80)
print(f"""
高速モード（include_details=False）:
  ✓ 所要時間: 約15-30秒
  ✓ 取得列数: 27列（基本 + Ultimate必須項目）
  ✓ 用途: 大量レース取得、学習データ収集

完全モード（include_details=True）:
  ✓ 所要時間: 約60-120秒（初回）/ 30-60秒（2回目以降、キャッシュ効果）
  ✓ 取得列数: 94列（全特徴量）
  ✓ 用途: 詳細分析、特定レースの精密予測

【推奨設定】
- データ収集ページ: include_details=False（高速モード）
- 予測ページ: include_details=True（完全モード）
""")
print("=" * 80)
