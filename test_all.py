"""
全機能の実動作テスト（絵文字なし）
"""
import sys
import os
from pathlib import Path
import requests
import json
import time

sys.path.insert(0, str(Path(__file__).parent / "keiba"))

def test_training():
    """1. モデル学習の実行テスト"""
    print("\n" + "="*60)
    print("TEST 1: モデル学習")
    print("="*60)
    
    try:
        url = "http://localhost:8000/api/train"
        payload = {
            "target": "win",
            "model_type": "logistic_regression",
            "test_size": 0.2,
            "cv_folds": 3,
            "use_sqlite": True
        }
        
        print("学習開始...")
        start_time = time.time()
        response = requests.post(url, json=payload, timeout=120)
        elapsed = time.time() - start_time
        
        if response.status_code == 200:
            result = response.json()
            print(f"\n[SUCCESS] 学習完了 ({elapsed:.1f}秒)")
            print(f"  モデルID: {result['model_id']}")
            print(f"  AUC: {result['metrics'].get('auc', 0):.4f}")
            print(f"  LogLoss: {result['metrics'].get('logloss', 0):.4f}")
            print(f"  データ数: {result['data_count']}")
            print(f"  特徴量数: {result['feature_count']}")
            return True, result['model_id']
        else:
            print(f"[FAIL] ステータス: {response.status_code}")
            print(f"  エラー: {response.text[:200]}")
            return False, None
            
    except Exception as e:
        print(f"[ERROR] {e}")
        return False, None

def test_prediction(model_id):
    """2. AI予測の実行テスト"""
    print("\n" + "="*60)
    print("TEST 2: AI予測")
    print("="*60)
    
    if not model_id:
        print("[SKIP] モデルIDがありません")
        return False
    
    try:
        url = "http://localhost:8000/api/predict"
        
        # ダミーデータで予測
        payload = {
            "model_id": model_id,
            "horses": [
                {"horse_number": 1, "horse_name": "TestHorse1", "odds": 3.5},
                {"horse_number": 2, "horse_name": "TestHorse2", "odds": 5.2},
                {"horse_number": 3, "horse_name": "TestHorse3", "odds": 8.1}
            ]
        }
        
        print(f"予測実行中（モデル: {model_id[:20]}...）")
        response = requests.post(url, json=payload, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            predictions = result.get('predictions', [])
            print(f"\n[SUCCESS] 予測完了")
            print(f"  予測数: {len(predictions)}")
            for i, pred in enumerate(predictions[:3], 1):
                prob = pred.get('probability', 0)
                print(f"  {i}位. 馬番{pred.get('horse_number')}: {prob:.2%}")
            return True
        else:
            print(f"[FAIL] ステータス: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"[ERROR] {e}")
        return False

def test_database():
    """3. データベース確認"""
    print("\n" + "="*60)
    print("TEST 3: データベース")
    print("="*60)
    
    try:
        import sqlite3
        from keiba_ai.config import load_config
        
        config = load_config(str(Path(__file__).parent / "keiba" / "config.yaml"))
        db_path = str(config.storage.sqlite_path)
        
        if not os.path.isabs(db_path):
            db_path = os.path.join(Path(__file__).parent / "keiba", db_path)
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM races")
        races = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM results")
        results = cursor.fetchone()[0]
        
        conn.close()
        
        print(f"[SUCCESS] データベース接続")
        print(f"  レース数: {races}")
        print(f"  結果レコード数: {results}")
        
        if races >= 10 and results >= 100:
            print("  データ量: 十分（学習可能）")
            return True
        else:
            print("  データ量: 不足（要データ収集）")
            return False
        
    except Exception as e:
        print(f"[ERROR] {e}")
        return False

def test_pages():
    """4. Webページアクセステスト"""
    print("\n" + "="*60)
    print("TEST 4: Webページ")
    print("="*60)
    
    pages = [
        ("トップ", "http://localhost:3000"),
        ("ダッシュボード", "http://localhost:3000/dashboard"),
        ("データ収集", "http://localhost:3000/data-collection"),
        ("学習", "http://localhost:3000/train"),
        ("予測", "http://localhost:3000/predict-batch"),
    ]
    
    success_count = 0
    for name, url in pages:
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                print(f"[OK] {name}: アクセス可能")
                success_count += 1
            else:
                print(f"[FAIL] {name}: {response.status_code}")
        except Exception as e:
            print(f"[ERROR] {name}: {e}")
    
    print(f"\n結果: {success_count}/{len(pages)} ページ正常")
    return success_count == len(pages)

def main():
    print("="*60)
    print("競馬AI Pro - 全機能動作テスト")
    print("="*60)
    
    # サーバー確認
    print("\n[CHECK] サーバー状態")
    try:
        requests.get("http://localhost:3000", timeout=5)
        print("  Next.js: 起動中")
    except:
        print("  Next.js: 未起動 - テスト中断")
        return
    
    try:
        requests.get("http://localhost:8000/api/models", timeout=5)
        print("  FastAPI: 起動中")
    except:
        print("  FastAPI: 未起動 - テスト中断")
        return
    
    # テスト実行
    results = {}
    
    results['database'] = test_database()
    time.sleep(1)
    
    results['training'], model_id = test_training()
    time.sleep(1)
    
    if model_id:
        results['prediction'] = test_prediction(model_id)
        time.sleep(1)
    else:
        results['prediction'] = False
    
    results['pages'] = test_pages()
    
    # 結果サマリー
    print("\n" + "="*60)
    print("テスト結果サマリー")
    print("="*60)
    print(f"  データベース: {'OK' if results['database'] else 'NG'}")
    print(f"  モデル学習: {'OK' if results['training'] else 'NG'}")
    print(f"  AI予測: {'OK' if results['prediction'] else 'NG'}")
    print(f"  ページ表示: {'OK' if results['pages'] else 'NG'}")
    
    success = sum(results.values())
    total = len(results)
    print(f"\n総合: {success}/{total} 成功")
    
    if success == total:
        print("\n[PASS] 全機能正常動作")
    else:
        print("\n[PARTIAL] 一部機能に問題あり")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nテスト中断")
    except Exception as e:
        print(f"\n[FATAL ERROR] {e}")
        import traceback
        traceback.print_exc()
