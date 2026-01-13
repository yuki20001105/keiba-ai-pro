"""
Ultimate版データを使って学習を実行
"""
import requests
import json
import time

API_URL = "http://localhost:8000/api/train"

print(f"\n{'='*80}")
print("【Ultimate版学習】")
print(f"{'='*80}\n")

# 学習リクエスト
train_request = {
    "target": "win",
    "model_type": "lightgbm",
    "test_size": 0.2,
    "cv_folds": 5,
    "use_sqlite": True,
    "ultimate_mode": True,  # Ultimate版モード ON
    "use_optimizer": True,
    "use_optuna": False,
    "optuna_trials": 50
}

print("設定:")
print(f"  Target: {train_request['target']}")
print(f"  Model: {train_request['model_type']}")
print(f"  Ultimate Mode: {train_request['ultimate_mode']}")
print(f"  Optimizer: {train_request['use_optimizer']}")
print()

print("→ 学習開始...")
print("  (数分かかります。お待ちください...)")
print()

start_time = time.time()

try:
    response = requests.post(
        API_URL,
        json=train_request,
        timeout=600  # 10分
    )
    
    # エラーレスポンスの詳細を表示
    if response.status_code != 200:
        print(f"[X] HTTP Error: {response.status_code}")
        try:
            error_detail = response.json()
            print(f"Error Details: {json.dumps(error_detail, indent=2, ensure_ascii=False)}")
        except:
            print(f"Response Text: {response.text}")
        raise Exception(f"HTTP {response.status_code}")
    
    response.raise_for_status()
    result = response.json()
    
    elapsed = time.time() - start_time
    
    print(f"\n{'='*80}")
    print("【学習完了】")
    print(f"{'='*80}\n")
    
    if result.get("success"):
        print("✓ 学習成功！")
        print(f"\nモデル情報:")
        print(f"  Model ID: {result.get('model_id')}")
        print(f"  Model Path: {result.get('model_path')}")
        print(f"\nデータ:")
        print(f"  Data Count: {result.get('data_count')}頭")
        print(f"  Race Count: {result.get('race_count')}レース")
        print(f"  Feature Count: {result.get('feature_count')}列")
        print(f"\n精度:")
        metrics = result.get('metrics', {})
        for key, value in metrics.items():
            print(f"  {key}: {value:.4f}")
        print(f"\n時間:")
        print(f"  Training Time: {result.get('training_time', 0):.1f}秒")
        print(f"  Total Time: {elapsed:.1f}秒")
    else:
        print(f"✗ 学習失敗")
        print(f"  Message: {result.get('message')}")
        
except requests.exceptions.Timeout:
    print("\n✗ タイムアウト（10分超過）")
    print("  → データ量が多い場合は時間がかかります")
except Exception as e:
    print(f"\n✗ エラー: {e}")
    
print()
