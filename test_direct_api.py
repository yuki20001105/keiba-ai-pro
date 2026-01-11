#!/usr/bin/env python
# -*- coding: utf-8 -*-
import sys
import json
import requests

print("="*80)
print("直接APIテスト")
print("="*80)

# テスト1: ヘルスチェック
print("\n【テスト1】ヘルスチェック")
try:
    r = requests.get("http://localhost:8001/health", timeout=5)
    print(f"✅ ステータス: {r.status_code}")
    print(f"   レスポンス: {r.json()}")
except Exception as e:
    print(f"❌ エラー: {e}")

# テスト2: レースデータ取得（2023年のレース）
print("\n【テスト2】レースデータ取得（202305010101）")
try:
    r = requests.post(
        "http://localhost:8001/scrape/ultimate",
        json={"race_id": "202305010101", "include_details": False},
        timeout=90
    )
    print(f"✅ ステータス: {r.status_code}")
    
    data = r.json()
    print(f"\n成功: {data.get('success')}")
    
    if data.get("success"):
        race_info = data.get("race_info", {})
        results = data.get("results", [])
        
        print(f"\n【レース情報】")
        print(f"  レース名: {race_info.get('race_name')}")
        print(f"  距離: {race_info.get('distance')}m")
        print(f"  トラック: {race_info.get('track_type')}")
        print(f"  天候: {race_info.get('weather')}")
        print(f"  馬場: {race_info.get('field_condition')}")
        print(f"  出走頭数: {len(results)}頭")
        
        if results:
            print(f"\n【上位3頭】")
            for i, result in enumerate(results[:3], 1):
                print(f"  {i}着: {result.get('horse_name')} ({result.get('finish_time')})")
        else:
            print("\n⚠️  結果データが0件です")
            print(f"   race_info keys: {list(race_info.keys())}")
    else:
        print(f"❌ エラー: {data.get('error')}")
        
except Exception as e:
    print(f"❌ エラー: {e}")
    import traceback
    traceback.print_exc()

# テスト3: 2024年のレース
print("\n【テスト3】レースデータ取得（202401041001）")
try:
    r = requests.post(
        "http://localhost:8001/scrape/ultimate",
        json={"race_id": "202401041001", "include_details": False},
        timeout=90
    )
    
    data = r.json()
    print(f"成功: {data.get('success')}")
    
    if data.get("success"):
        race_info = data.get("race_info", {})
        results = data.get("results", [])
        
        print(f"  レース名: {race_info.get('race_name')}")
        print(f"  距離: {race_info.get('distance')}m")
        print(f"  出走頭数: {len(results)}頭")
        
        if results:
            print(f"  1着: {results[0].get('horse_name')}")
    else:
        print(f"❌ エラー: {data.get('error')}")
        
except Exception as e:
    print(f"❌ エラー: {e}")

print("\n" + "="*80)
print("テスト完了")
print("="*80)
