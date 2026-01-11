"""
シンプルなOptunaテストスクリプト
requests を使用してAPIを呼び出し、結果を確認
"""
import requests
import json
from datetime import datetime

print("\n" + "=" * 80)
print("=== Optuna テスト開始 ===")
print("=" * 80)

# リクエストデータ
request_data = {
    "target": "win",
    "model_type": "lightgbm",
    "use_optimizer": True,
    "use_optuna": True,
    "optuna_trials": 3,
    "cv_folds": 2,
    "test_size": 0.2,
    "use_sqlite": True
}

print(f"\nリクエスト内容:")
print(json.dumps(request_data, indent=2, ensure_ascii=False))

# FastAPI 起動確認
print("\nFastAPI 接続確認...")
try:
    response = requests.get("http://localhost:8000/", timeout=5)
    print(f"✓ FastAPI 接続成功: {response.json()['status']}")
except Exception as e:
    print(f"❌ FastAPI 接続失敗: {e}")
    print("\nFastAPIを起動してから再実行してください:")
    print("cd C:\\Users\\yuki2\\Documents\\ws\\keiba-ai-pro\\python-api")
    print("$env:PYTHONPATH=\"C:\\Users\\yuki2\\Documents\\ws\\keiba-ai-pro\"")
    print("python -m uvicorn main:app --host 0.0.0.0 --port 8000")
    exit(1)

# リクエスト送信
print("\nトレーニングリクエストを送信...")
start_time = datetime.now()

try:
    response = requests.post(
        "http://localhost:8000/api/train",
        json=request_data,
        timeout=180
    )
    
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    
    print(f"\n✓ リクエスト成功 (HTTP {response.status_code})")
    print(f"実行時間: {duration:.2f} 秒")
    
    result = response.json()
    
    print("\n" + "=" * 80)
    print("=== 結果 ===")
    print("=" * 80)
    print(f"optuna_executed: {result.get('optuna_executed', 'N/A')}")
    print(f"optuna_error: {result.get('optuna_error', 'なし')}")
    print(f"training_time: {result.get('training_time', 'N/A')}")
    print(f"auc: {result.get('auc', 'N/A'):.4f}" if result.get('auc') else "auc: N/A")
    print(f"cv_auc_mean: {result.get('cv_auc_mean', 'N/A'):.4f}" if result.get('cv_auc_mean') else "cv_auc_mean: N/A")
    
    # 判定
    print("\n" + "=" * 80)
    print("=== 判定 ===")
    print("=" * 80)
    
    if result.get('optuna_executed') and duration > 10:
        print("✓ Optunaが正常に実行されました！")
        print(f"   実行時間が{duration:.2f}秒で、3試行×2フォールド=6回の学習が行われた可能性が高いです")
    elif result.get('optuna_executed') and duration < 5:
        print("❌ optuna_executed=True ですが、実行時間が短すぎます")
        print(f"   実行時間: {duration:.2f}秒 → Optunaの optimize() が実行されていない可能性")
        print("   ログファイルを確認してください:")
        print("   C:\\Users\\yuki2\\Documents\\ws\\keiba-ai-pro\\optuna_debug.log")
    else:
        print("❌ Optunaが実行されませんでした")
        if result.get('optuna_error'):
            print(f"   エラー: {result.get('optuna_error')}")
    
except requests.exceptions.Timeout:
    print("\n❌ タイムアウト: リクエストが180秒以内に完了しませんでした")
    print("   これは正常な場合があります（Optunaが正しく実行されている可能性）")
    
except Exception as e:
    print(f"\n❌ エラー: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 80)
print("テスト完了")
print("=" * 80)
