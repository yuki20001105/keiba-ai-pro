"""
LightGBM最適化機能の統合テスト
FastAPI経由での学習・予測をテスト
"""

import requests
import json
from pprint import pprint

BASE_URL = "http://localhost:8000"


def test_optimized_training():
    """最適化モードでの学習をテスト"""
    print("\n" + "="*80)
    print("【1. LightGBM最適化モードでの学習テスト】")
    print("="*80)
    
    # 学習リクエスト
    payload = {
        "target": "win",
        "model_type": "lightgbm",
        "test_size": 0.2,
        "cv_folds": 3,
        "use_sqlite": True,
        "ultimate_mode": False,
        "use_optimizer": True  # 最適化ON
    }
    
    print("\nリクエスト:")
    pprint(payload)
    
    try:
        response = requests.post(
            f"{BASE_URL}/api/train",
            json=payload,
            timeout=300
        )
        
        if response.status_code == 200:
            result = response.json()
            print("\n✅ 学習成功:")
            print(f"  モデルID: {result['model_id']}")
            print(f"  AUC: {result['metrics']['auc']:.4f}")
            print(f"  LogLoss: {result['metrics']['logloss']:.4f}")
            print(f"  CV AUC: {result['metrics']['cv_auc_mean']:.4f} ± {result['metrics']['cv_auc_std']:.4f}")
            print(f"  データ数: {result['data_count']}行")
            print(f"  レース数: {result['race_count']}レース")
            print(f"  特徴量数: {result['feature_count']}列")
            print(f"  学習時間: {result['training_time']:.2f}秒")
            return result['model_id']
        else:
            print(f"\n❌ エラー: {response.status_code}")
            print(response.text)
            return None
            
    except Exception as e:
        print(f"\n❌ 例外発生: {e}")
        return None


def test_standard_training():
    """標準モードでの学習をテスト（比較用）"""
    print("\n" + "="*80)
    print("【2. 標準モードでの学習テスト（比較用）】")
    print("="*80)
    
    payload = {
        "target": "win",
        "model_type": "lightgbm",
        "test_size": 0.2,
        "cv_folds": 3,
        "use_sqlite": True,
        "ultimate_mode": False,
        "use_optimizer": False  # 最適化OFF
    }
    
    print("\nリクエスト:")
    pprint(payload)
    
    try:
        response = requests.post(
            f"{BASE_URL}/api/train",
            json=payload,
            timeout=300
        )
        
        if response.status_code == 200:
            result = response.json()
            print("\n✅ 学習成功:")
            print(f"  モデルID: {result['model_id']}")
            print(f"  AUC: {result['metrics']['auc']:.4f}")
            print(f"  LogLoss: {result['metrics']['logloss']:.4f}")
            print(f"  CV AUC: {result['metrics']['cv_auc_mean']:.4f} ± {result['metrics']['cv_auc_std']:.4f}")
            print(f"  学習時間: {result['training_time']:.2f}秒")
            return result['model_id']
        else:
            print(f"\n❌ エラー: {response.status_code}")
            print(response.text)
            return None
            
    except Exception as e:
        print(f"\n❌ 例外発生: {e}")
        return None


def test_prediction(model_id):
    """予測をテスト"""
    print("\n" + "="*80)
    print("【3. 予測テスト】")
    print("="*80)
    
    # サンプルデータ
    horses = [
        {
            "horse_number": 1,
            "horse_name": "テスト馬1",
            "age": 4,
            "sex": "牡",
            "weight": 480,
            "weight_diff": 0,
            "handicap": 54.0,
            "entry_odds": 3.5,
            "entry_popularity": 2
        },
        {
            "horse_number": 2,
            "horse_name": "テスト馬2",
            "age": 3,
            "sex": "牝",
            "weight": 450,
            "weight_diff": -5,
            "handicap": 52.0,
            "entry_odds": 8.2,
            "entry_popularity": 5
        },
        {
            "horse_number": 3,
            "horse_name": "テスト馬3",
            "age": 5,
            "sex": "牡",
            "weight": 500,
            "weight_diff": 10,
            "handicap": 56.0,
            "entry_odds": 2.1,
            "entry_popularity": 1
        }
    ]
    
    payload = {
        "model_id": model_id,
        "horses": horses
    }
    
    print(f"\nモデルID: {model_id}")
    print(f"馬数: {len(horses)}頭")
    
    try:
        response = requests.post(
            f"{BASE_URL}/api/predict",
            json=payload,
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            print("\n✅ 予測成功:")
            print(f"\n予測結果（上位3頭）:")
            for pred in result['predictions'][:3]:
                print(f"  {pred['predicted_rank']}位: {pred['horse_name']:10s} "
                      f"確率={pred['probability']:.4f} オッズ={pred['odds']:.1f}")
            return True
        else:
            print(f"\n❌ エラー: {response.status_code}")
            print(response.text)
            return False
            
    except Exception as e:
        print(f"\n❌ 例外発生: {e}")
        return False


def test_model_list():
    """モデル一覧をテスト"""
    print("\n" + "="*80)
    print("【4. モデル一覧テスト】")
    print("="*80)
    
    try:
        response = requests.get(f"{BASE_URL}/api/models", timeout=10)
        
        if response.status_code == 200:
            result = response.json()
            print(f"\n✅ モデル一覧取得成功:")
            print(f"  モデル数: {result['count']}個\n")
            
            for i, model in enumerate(result['models'][:5], 1):
                opt_label = "🚀最適化" if model.get('use_optimizer') else "標準"
                print(f"  {i}. {model['model_id']}")
                print(f"     タイプ: {model['model_type']} ({opt_label})")
                print(f"     AUC: {model['auc']:.4f}")
                print(f"     CV AUC: {model['cv_auc_mean']:.4f}")
                print()
            
            return True
        else:
            print(f"\n❌ エラー: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"\n❌ 例外発生: {e}")
        return False


def compare_performance():
    """最適化版と標準版のパフォーマンスを比較"""
    print("\n" + "="*80)
    print("【5. パフォーマンス比較】")
    print("="*80)
    
    try:
        response = requests.get(f"{BASE_URL}/api/models", timeout=10)
        
        if response.status_code == 200:
            result = response.json()
            models = result['models']
            
            optimized = [m for m in models if m.get('use_optimizer')]
            standard = [m for m in models if not m.get('use_optimizer')]
            
            print(f"\n最適化モデル: {len(optimized)}個")
            if optimized:
                avg_auc = sum(m['auc'] for m in optimized) / len(optimized)
                print(f"  平均AUC: {avg_auc:.4f}")
            
            print(f"\n標準モデル: {len(standard)}個")
            if standard:
                avg_auc = sum(m['auc'] for m in standard) / len(standard)
                print(f"  平均AUC: {avg_auc:.4f}")
            
            if optimized and standard:
                opt_best = max(m['auc'] for m in optimized)
                std_best = max(m['auc'] for m in standard)
                improvement = ((opt_best - std_best) / std_best) * 100
                print(f"\n最良モデル比較:")
                print(f"  最適化版: {opt_best:.4f}")
                print(f"  標準版: {std_best:.4f}")
                print(f"  改善率: {improvement:+.2f}%")
            
            return True
        else:
            return False
            
    except Exception as e:
        print(f"\n❌ 例外発生: {e}")
        return False


if __name__ == "__main__":
    print("\n" + "■"*40)
    print("  LightGBM最適化機能 統合テスト")
    print("■"*40)
    
    # 1. 最適化モードで学習
    optimized_model_id = test_optimized_training()
    
    # 2. 標準モードで学習（比較用）
    standard_model_id = test_standard_training()
    
    # 3. 予測テスト
    if optimized_model_id:
        test_prediction(optimized_model_id)
    
    # 4. モデル一覧
    test_model_list()
    
    # 5. パフォーマンス比較
    compare_performance()
    
    print("\n" + "="*80)
    print("【テスト完了】")
    print("="*80)
    print("\n次のステップ:")
    print("  1. フロントエンドから最適化モードで学習を実行")
    print("  2. AUCの改善を確認")
    print("  3. 予測速度の改善を確認")
    print()
