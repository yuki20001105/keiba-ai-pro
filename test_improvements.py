"""改善版スクレイピングのテスト"""
import requests
import json

print("\n" + "="*80)
print("  改善版スクレイピング機能テスト")
print("="*80)

# テスト1: 基本機能（性齢・コーナーパース）
print("\n【テスト1: 性齢・コーナー通過順のパース】")
try:
    response = requests.post(
        "http://localhost:8001/scrape/ultimate",
        json={
            "race_id": "202305010101",
            "include_details": False,
            "include_shutuba": False
        },
        timeout=60
    )
    data = response.json()
    
    if data['success'] and len(data['results']) > 0:
        first = data['results'][0]
        print(f"✓ データ取得成功: {len(data['results'])}頭")
        print(f"\n【1着馬: {first['horse_name']}】")
        print(f"  性齢 (元)      : {first.get('sex_age', 'N/A')}")
        print(f"  → 性別        : {first.get('sex', 'N/A')}")
        print(f"  → 年齢        : {first.get('age', 'N/A')}")
        print(f"  コーナー (元)  : {first.get('corner_positions', 'N/A')}")
        print(f"  → 配列化      : {first.get('corner_positions_list', 'N/A')}")
        print(f"  上がり3F      : {first.get('last_3f', 'N/A')} ({first.get('last_3f_rank', 'N/A')}位)")
        
        print(f"\n【レース情報】")
        print(f"  距離          : {data['race_info'].get('distance', 'N/A')}m")
        print(f"  トラック      : {data['race_info'].get('track_type', 'N/A')}")
        print(f"  ペース区分    : {data['race_info'].get('pace_classification', 'N/A')}")
        
        # 改善検証
        improvements = []
        if first.get('sex') and first.get('age'):
            improvements.append("✓ 性齢パース成功")
        else:
            improvements.append("✗ 性齢パース失敗")
        
        if isinstance(first.get('corner_positions_list'), list) and len(first.get('corner_positions_list', [])) > 0:
            improvements.append("✓ コーナー通過順パース成功")
        else:
            improvements.append("✗ コーナー通過順パース失敗")
        
        if data['race_info'].get('pace_classification'):
            improvements.append("✓ ペース区分取得成功")
        else:
            improvements.append("⚠ ペース区分取得失敗（ページに記載がない可能性）")
        
        print(f"\n【改善状況】")
        for imp in improvements:
            print(f"  {imp}")
    else:
        print(f"✗ データ取得失敗: {data.get('error', 'Unknown error')}")

except Exception as e:
    print(f"✗ エラー: {e}")

print("\n" + "="*80)
