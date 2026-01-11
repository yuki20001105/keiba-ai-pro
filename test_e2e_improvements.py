"""
エンドツーエンドテスト: データ収集 → 学習 → 予測
改善されたスクレイピング機能の統合確認
"""
import requests
import json
import time

print("\n" + "="*80)
print("  【Ultimate版】エンドツーエンドテスト")
print("="*80)

# テスト設定
ULTIMATE_SERVICE_PORT = 8001
TEST_RACE_ID = "202305010101"

def test_step_1_data_collection():
    """ステップ1: データ収集（改善された特徴量を含む）"""
    print("\n【ステップ1: データ収集】")
    print("-" * 80)
    
    try:
        print(f"  Race ID: {TEST_RACE_ID}")
        print(f"  include_details: True (近走データ派生特徴を含む)")
        print(f"  include_shutuba: False")
        print("  実行中...")
        
        response = requests.post(
            f"http://localhost:{ULTIMATE_SERVICE_PORT}/scrape/ultimate",
            json={
                "race_id": TEST_RACE_ID,
                "include_details": True,  # 近走データ派生特徴を取得
                "include_shutuba": False
            },
            timeout=120
        )
        
        if response.status_code != 200:
            print(f"  ✗ エラー: HTTP {response.status_code}")
            return None
        
        data = response.json()
        
        if not data.get('success'):
            print(f"  ✗ スクレイピング失敗: {data.get('error')}")
            return None
        
        print(f"  ✓ 取得成功！")
        print(f"    - 頭数: {len(data['results'])}頭")
        
        # 改善された特徴量の確認
        if len(data['results']) > 0:
            first_horse = data['results'][0]
            print(f"\n  【サンプル馬: {first_horse['horse_name']}】")
            
            features_check = {
                "✅ 性別": first_horse.get('sex', 'N/A'),
                "✅ 年齢": first_horse.get('age', 'N/A'),
                "✅ コーナー通過順（配列）": first_horse.get('corner_positions_list', 'N/A'),
                "✅ 上がり3F順位": first_horse.get('last_3f_rank', 'N/A'),
            }
            
            for key, value in features_check.items():
                print(f"    {key}: {value}")
            
            # 近走派生特徴（include_details=Trueの場合）
            past_features = first_horse.get('past_performance_features', {})
            if past_features:
                print(f"\n  【近走派生特徴】")
                print(f"    - 前走からの日数: {past_features.get('days_since_last_race', 'N/A')}")
                print(f"    - 距離変化: {past_features.get('last_distance_change', 'N/A')}")
                print(f"    - 人気トレンド: {past_features.get('popularity_trend', 'N/A')}")
        
        # レース情報
        print(f"\n  【レース情報】")
        print(f"    - ペース区分: {data['race_info'].get('pace_classification', 'N/A')}")
        print(f"    - 距離: {data['race_info'].get('distance', 'N/A')}m")
        print(f"    - トラック: {data['race_info'].get('track_type', 'N/A')}")
        
        # 派生特徴
        derived = data.get('derived_features', {})
        if derived:
            print(f"\n  【派生特徴】")
            if 'pace_diff' in derived:
                print(f"    - ペース差分: {derived['pace_diff']:.2f}")
            if 'market_entropy' in derived:
                print(f"    - マーケットエントロピー: {derived['market_entropy']:.3f}")
        
        return data
        
    except requests.exceptions.Timeout:
        print(f"  ✗ タイムアウト: サービスが応答しません")
        return None
    except Exception as e:
        print(f"  ✗ エラー: {e}")
        import traceback
        traceback.print_exc()
        return None


def test_step_2_feature_validation(scrape_data):
    """ステップ2: 特徴量の検証"""
    print("\n【ステップ2: 特徴量検証】")
    print("-" * 80)
    
    if not scrape_data or not scrape_data.get('success'):
        print("  ✗ スクレイピングデータなし")
        return False
    
    results = scrape_data.get('results', [])
    if len(results) == 0:
        print("  ✗ 結果データなし")
        return False
    
    # 新機能の検証
    checks = []
    
    # 1. 性齢パース
    sex_parsed = all('sex' in h and 'age' in h for h in results)
    checks.append(("性齢パース", sex_parsed))
    
    # 2. コーナー通過順配列化
    corner_parsed = all('corner_positions_list' in h for h in results)
    checks.append(("コーナー通過順配列化", corner_parsed))
    
    # 3. 上がり順位
    rank_calculated = all('last_3f_rank' in h for h in results)
    checks.append(("上がり3F順位計算", rank_calculated))
    
    # 4. ペース区分
    pace_exists = 'pace_classification' in scrape_data.get('race_info', {})
    checks.append(("ペース区分取得", pace_exists))
    
    # 5. 派生特徴
    derived_exists = len(scrape_data.get('derived_features', {})) > 0
    checks.append(("派生特徴計算", derived_exists))
    
    print("  【検証結果】")
    all_passed = True
    for name, passed in checks:
        status = "✓" if passed else "✗"
        print(f"    {status} {name}: {'合格' if passed else '失敗'}")
        if not passed:
            all_passed = False
    
    return all_passed


def test_step_3_ml_compatibility():
    """ステップ3: 機械学習互換性テスト"""
    print("\n【ステップ3: 機械学習互換性】")
    print("-" * 80)
    
    print("  特徴量エンジニアリング関数のテスト...")
    
    try:
        import sys
        sys.path.insert(0, r"C:\Users\yuki2\Documents\ws\keiba-ai-pro")
        sys.path.insert(0, r"C:\Users\yuki2\Documents\ws\keiba-ai-pro\keiba")
        
        from keiba_ai.feature_engineering import add_derived_features
        import pandas as pd
        import numpy as np
        
        # テストデータ作成
        test_df = pd.DataFrame({
            'race_id': ['202305010101'] * 3,
            'horse_name': ['馬A', '馬B', '馬C'],
            'sex': ['牡', '牝', '牡'],
            'age': [3, 4, 5],
            'corner_positions_list': [[1, 2, 1, 1], [5, 4, 3, 2], [3, 3, 3, 3]],
            'last_3f_rank': [1, 2, 3],
            'days_since_last_race': [14, 30, 60],
            'last_distance_change': [200, -200, 0],
            'popularity_trend': ['improving', 'declining', 'stable'],
            'pace_classification': ['H', 'M', 'S'],
            'num_horses': [16, 16, 16],
            'distance': [1400, 1400, 1400],
            'surface': ['turf', 'turf', 'turf']
        })
        
        print(f"    入力データ: {len(test_df)} 行")
        
        # 特徴量エンジニアリング実行
        result_df = add_derived_features(test_df)
        
        print(f"    出力データ: {len(result_df)} 行, {len(result_df.columns)} 列")
        
        # 新機能による追加特徴量を確認
        new_features = []
        if 'sex_牡' in result_df.columns:
            new_features.append('性別ダミー変数')
        if 'is_young' in result_df.columns:
            new_features.append('年齢カテゴリ')
        if 'corner_position_avg' in result_df.columns:
            new_features.append('コーナー平均位置')
        if 'position_change' in result_df.columns:
            new_features.append('ポジション変化')
        if 'pace_H' in result_df.columns or 'pace_M' in result_df.columns:
            new_features.append('ペースダミー変数')
        if 'rest_short' in result_df.columns:
            new_features.append('休養期間カテゴリ')
        if 'distance_increased' in result_df.columns:
            new_features.append('距離変化フラグ')
        
        print(f"\n  【生成された新特徴量】")
        for feature in new_features:
            print(f"    ✓ {feature}")
        
        print(f"\n  ✓ 特徴量エンジニアリング成功！")
        print(f"    元のカラム数: {len(test_df.columns)}")
        print(f"    処理後カラム数: {len(result_df.columns)}")
        print(f"    追加された特徴: {len(result_df.columns) - len(test_df.columns)}個")
        
        return True
        
    except Exception as e:
        print(f"  ✗ エラー: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    print("\n🎯 改善されたスクレイピング機能の統合テストを開始します\n")
    
    # ステップ1: データ収集
    scrape_data = test_step_1_data_collection()
    
    if scrape_data:
        time.sleep(1)
        
        # ステップ2: 特徴量検証
        features_valid = test_step_2_feature_validation(scrape_data)
        
        time.sleep(1)
        
        # ステップ3: 機械学習互換性
        ml_compatible = test_step_3_ml_compatibility()
        
        # 総合評価
        print("\n" + "="*80)
        print("  【総合評価】")
        print("="*80)
        
        if scrape_data and features_valid and ml_compatible:
            print("\n  ✅ すべてのテストに合格しました！")
            print("\n  【次のステップ】")
            print("    1. データ収集UIで実際にデータを収集")
            print("    2. 学習機能で新しい特徴量を使ってモデル学習")
            print("    3. 予測機能で学習済みモデルを使って予測")
        else:
            print("\n  ⚠️ 一部のテストが失敗しました")
            print("    - データ収集: ", "✓" if scrape_data else "✗")
            print("    - 特徴量検証: ", "✓" if features_valid else "✗")
            print("    - ML互換性: ", "✓" if ml_compatible else "✗")
    else:
        print("\n  ✗ データ収集に失敗したため、テストを中断しました")
        print("\n  【確認事項】")
        print("    - Ultimateサービスが起動していますか？(port 8001)")
        print("    - race_id は正しいですか？")
    
    print("\n" + "="*80 + "\n")


if __name__ == "__main__":
    main()
