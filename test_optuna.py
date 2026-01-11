"""
Optuna最適化機能のテストスクリプト

LightGBM特徴量最適化 + Optunaハイパーパラメータ最適化をテストします。
"""

import requests
import time
from pprint import pprint


BASE_URL = "http://localhost:8000"


def print_section(title):
    """セクションタイトルを表示"""
    print("\n" + "="*80)
    print(f"  {title}")
    print("="*80 + "\n")


def test_optuna_training():
    """Optuna最適化での学習をテスト"""
    print_section("【1. Optuna最適化モードでの学習テスト】")
    
    request_data = {
        "target": "win",
        "model_type": "lightgbm",
        "test_size": 0.2,
        "cv_folds": 3,  # 高速化のため3フォールド
        "use_sqlite": True,
        "ultimate_mode": False,
        "use_optimizer": True,  # LightGBM特徴量最適化を使用
        "use_optuna": True,     # Optunaハイパーパラメータ最適化を使用
        "optuna_trials": 20     # テストのため20試行
    }
    
    print("リクエスト:")
    pprint(request_data)
    print()
    
    try:
        start_time = time.time()
        response = requests.post(
            f"{BASE_URL}/api/train",
            json=request_data,
            timeout=600  # Optunaは時間がかかるので10分
        )
        elapsed_time = time.time() - start_time
        
        if response.status_code == 200:
            result = response.json()
            print("✅ 学習成功:")
            print(f"  モデルID: {result['model_id']}")
            print(f"  AUC: {result['metrics']['auc']:.4f}")
            print(f"  LogLoss: {result['metrics']['logloss']:.4f}")
            print(f"  CV AUC: {result['metrics']['cv_auc_mean']:.4f} ± {result['metrics']['cv_auc_std']:.4f}")
            print(f"  データ数: {result['data_count']}行")
            print(f"  レース数: {result['race_count']}レース")
            print(f"  特徴量数: {result['feature_count']}列")
            print(f"  学習時間: {elapsed_time:.2f}秒")
            return result['model_id']
        else:
            print(f"❌ エラー: {response.status_code}")
            print(response.json())
            return None
            
    except Exception as e:
        print(f"❌ エラー: {str(e)}")
        return None


def test_standard_training():
    """標準モード（Optunaなし）での学習をテスト"""
    print_section("【2. 標準モード（Optunaなし）での学習テスト】")
    
    request_data = {
        "target": "win",
        "model_type": "lightgbm",
        "test_size": 0.2,
        "cv_folds": 3,
        "use_sqlite": True,
        "ultimate_mode": False,
        "use_optimizer": True,  # LightGBM特徴量最適化を使用
        "use_optuna": False     # Optunaなし
    }
    
    print("リクエスト:")
    pprint(request_data)
    print()
    
    try:
        start_time = time.time()
        response = requests.post(
            f"{BASE_URL}/api/train",
            json=request_data,
            timeout=120
        )
        elapsed_time = time.time() - start_time
        
        if response.status_code == 200:
            result = response.json()
            print("✅ 学習成功:")
            print(f"  モデルID: {result['model_id']}")
            print(f"  AUC: {result['metrics']['auc']:.4f}")
            print(f"  LogLoss: {result['metrics']['logloss']:.4f}")
            print(f"  CV AUC: {result['metrics']['cv_auc_mean']:.4f} ± {result['metrics']['cv_auc_std']:.4f}")
            print(f"  学習時間: {elapsed_time:.2f}秒")
            return result['model_id']
        else:
            print(f"❌ エラー: {response.status_code}")
            print(response.json())
            return None
            
    except Exception as e:
        print(f"❌ エラー: {str(e)}")
        return None


def test_model_comparison():
    """モデル一覧を取得して比較"""
    print_section("【3. モデルパフォーマンス比較】")
    
    try:
        response = requests.get(f"{BASE_URL}/api/models")
        
        if response.status_code == 200:
            result = response.json()
            models = result.get('models', [])
            
            print(f"✅ モデル一覧取得成功:")
            print(f"  モデル数: {len(models)}個\n")
            
            # Optunaモデルと標準モデルを分類
            optuna_models = []
            standard_optimized_models = []
            standard_models = []
            
            for model in models[:10]:  # 最新10個を表示
                model_id = model.get('model_id', 'unknown')
                model_type = model.get('model_type', 'unknown')
                use_optimizer = model.get('use_optimizer', False)
                auc = model.get('metrics', {}).get('auc', 0)
                cv_auc = model.get('metrics', {}).get('cv_auc_mean', 0)
                
                # ファイル名からOptunaモデルを判定
                is_optuna = '_optuna' in model_id or 'optuna' in model_id.lower()
                
                if is_optuna:
                    optuna_models.append((model_id, auc, cv_auc))
                elif use_optimizer:
                    standard_optimized_models.append((model_id, auc, cv_auc))
                else:
                    standard_models.append((model_id, auc, cv_auc))
                
                mode_label = "🔥Optuna+最適化" if is_optuna else ("🚀最適化" if use_optimizer else "標準")
                print(f"  {model_id}")
                print(f"    タイプ: {model_type} ({mode_label})")
                print(f"    AUC: {auc:.4f}")
                print(f"    CV AUC: {cv_auc:.4f}\n")
            
            # 統計情報
            print("\n【統計情報】")
            
            if optuna_models:
                avg_auc = sum(m[1] for m in optuna_models) / len(optuna_models)
                best_auc = max(m[1] for m in optuna_models)
                print(f"Optunaモデル: {len(optuna_models)}個")
                print(f"  平均AUC: {avg_auc:.4f}")
                print(f"  最良AUC: {best_auc:.4f}\n")
            
            if standard_optimized_models:
                avg_auc = sum(m[1] for m in standard_optimized_models) / len(standard_optimized_models)
                best_auc = max(m[1] for m in standard_optimized_models)
                print(f"最適化モデル（Optunaなし）: {len(standard_optimized_models)}個")
                print(f"  平均AUC: {avg_auc:.4f}")
                print(f"  最良AUC: {best_auc:.4f}\n")
            
            if standard_models:
                avg_auc = sum(m[1] for m in standard_models) / len(standard_models)
                best_auc = max(m[1] for m in standard_models)
                print(f"標準モデル: {len(standard_models)}個")
                print(f"  平均AUC: {avg_auc:.4f}")
                print(f"  最良AUC: {best_auc:.4f}\n")
            
            # 比較
            if optuna_models and standard_optimized_models:
                optuna_best = max(m[1] for m in optuna_models)
                standard_best = max(m[1] for m in standard_optimized_models)
                improvement = ((optuna_best - standard_best) / standard_best) * 100
                
                print("【Optuna vs 標準最適化】")
                print(f"  Optuna最良: {optuna_best:.4f}")
                print(f"  標準最良: {standard_best:.4f}")
                print(f"  改善率: {improvement:+.2f}%")
            
        else:
            print(f"❌ エラー: {response.status_code}")
            print(response.json())
            
    except Exception as e:
        print(f"❌ エラー: {str(e)}")


def main():
    """メインテスト実行"""
    print("\n" + "■"*80)
    print("  Optuna最適化機能 統合テスト")
    print("■"*80)
    
    # APIサーバー接続確認
    try:
        response = requests.get(f"{BASE_URL}/")
        if response.status_code != 200:
            print("\n❌ FastAPIサーバーに接続できません")
            print("python-api/main.py が起動していることを確認してください")
            return
    except:
        print("\n❌ FastAPIサーバーに接続できません")
        print("python-api/main.py が起動していることを確認してください")
        return
    
    # テスト実行
    print("\n⚠️  注意: Optuna最適化は時間がかかります（数分〜10分程度）")
    print("FastAPIのコンソール出力で進捗を確認できます\n")
    
    input("Enterキーを押して開始...")
    
    # 1. Optuna最適化モデルを学習
    optuna_model_id = test_optuna_training()
    
    # 2. 標準モデルを学習（比較用）
    standard_model_id = test_standard_training()
    
    # 3. モデル比較
    test_model_comparison()
    
    # まとめ
    print_section("【テスト完了】")
    print("次のステップ:")
    print("  1. フロントエンドから use_optuna=true で学習を実行")
    print("  2. AUCの改善を確認")
    print("  3. 最適化されたパラメータで予測を実行")
    print("\nOptuna最適化のメリット:")
    print("  ✓ ハイパーパラメータを自動最適化")
    print("  ✓ 予測精度の向上（通常1-3%改善）")
    print("  ✓ 過学習の抑制")
    print("  ✓ モデルの安定性向上")


if __name__ == "__main__":
    main()
