"""完全な改善検証テスト"""
import requests
import json
import time

print("\n" + "="*80)
print("  Ultimate版スクレイピング 改善検証テスト")
print("="*80)

# テスト対象のレースID
race_id = "202305010101"

print(f"\n【テスト対象】")
print(f"  Race ID: {race_id}")
print(f"  実行中... (30秒程度かかります)")

try:
    response = requests.post(
        "http://localhost:8001/scrape/ultimate",
        json={
            "race_id": race_id,
            "include_details": False,
            "include_shutuba": False
        },
        timeout=90
    )
    data = response.json()
    
    print(f"\n{'='*80}")
    print(f"  テスト結果")
    print(f"{'='*80}")
    
    if not data['success']:
        print(f"✗ スクレイピング失敗: {data.get('error', 'Unknown error')}")
        exit(1)
    
    print(f"\n✓ スクレイピング成功！")
    print(f"  取得頭数: {len(data['results'])}頭")
    
    # 改善1: 性齢パース
    print(f"\n【改善1: 性齢パース（性別・年齢の分離）】")
    success_count = 0
    for i, horse in enumerate(data['results'][:5], 1):
        sex = horse.get('sex')
        age = horse.get('age')
        sex_age_raw = horse.get('sex_age', '')
        if sex and age:
            print(f"  {i}. {horse['horse_name']:12s} | 元: {sex_age_raw:4s} → 性別: {sex}, 年齢: {age}")
            success_count += 1
        else:
            print(f"  {i}. {horse['horse_name']:12s} | ✗ パース失敗")
    
    print(f"  → 成功率: {success_count}/{min(5, len(data['results']))}")
    
    # 改善2: コーナー通過順パース
    print(f"\n【改善2: コーナー通過順パース（配列化）】")
    success_count = 0
    for i, horse in enumerate(data['results'][:5], 1):
        corner_list = horse.get('corner_positions_list', [])
        corner_raw = horse.get('corner_positions', '')
        if isinstance(corner_list, list) and len(corner_list) > 0:
            print(f"  {i}. {horse['horse_name']:12s} | 元: {corner_raw:8s} → 配列: {corner_list}")
            success_count += 1
        elif corner_raw and corner_raw != '-':
            print(f"  {i}. {horse['horse_name']:12s} | ✗ パース失敗: {corner_raw}")
        else:
            print(f"  {i}. {horse['horse_name']:12s} | データなし")
    
    print(f"  → 成功率: {success_count}/{min(5, len(data['results']))}")
    
    # 改善3: 上がり順位
    print(f"\n【改善3: 上がり3F順位（自動計算）】")
    for i, horse in enumerate(data['results'][:5], 1):
        last_3f = horse.get('last_3f', 'N/A')
        rank = horse.get('last_3f_rank', 'N/A')
        print(f"  {i}. {horse['horse_name']:12s} | 上がり: {last_3f}秒 → 順位: {rank}位")
    
    # 改善4: ペース区分
    print(f"\n【改善4: ペース区分抽出（H/M/S）】")
    pace = data['race_info'].get('pace_classification')
    if pace:
        print(f"  ✓ ペース区分: {pace}")
    else:
        print(f"  ⚠ ペース区分: 未取得（ページに記載がない可能性）")
    
    # 改善5: 派生特徴
    print(f"\n【改善5: 派生特徴計算】")
    derived = data.get('derived_features', {})
    if 'pace_diff' in derived:
        print(f"  ✓ ペース差分: {derived['pace_diff']:.2f}")
    if 'market_entropy' in derived:
        print(f"  ✓ マーケットエントロピー: {derived['market_entropy']:.3f}")
    if 'top3_probability' in derived:
        print(f"  ✓ 上位3頭確率合計: {derived['top3_probability']:.3f}")
    
    # 総合評価
    print(f"\n{'='*80}")
    print(f"  総合評価")
    print(f"{'='*80}")
    
    checks = [
        ("性齢パース", all(h.get('sex') and h.get('age') for h in data['results'][:5])),
        ("コーナーパース", all(isinstance(h.get('corner_positions_list'), list) for h in data['results'][:5])),
        ("上がり順位", all(h.get('last_3f_rank') for h in data['results'][:5])),
        ("派生特徴計算", 'market_entropy' in derived),
    ]
    
    for name, passed in checks:
        status = "✓" if passed else "✗"
        color = "passed" if passed else "failed"
        print(f"  {status} {name}: {'合格' if passed else '要改善'}")
    
    passed_count = sum(1 for _, p in checks if p)
    print(f"\n  合格率: {passed_count}/{len(checks)} ({passed_count/len(checks)*100:.1f}%)")
    
except requests.exceptions.Timeout:
    print(f"\n✗ タイムアウト: サービスが応答しません")
except Exception as e:
    print(f"\n✗ エラー: {e}")
    import traceback
    traceback.print_exc()

print(f"\n{'='*80}\n")
