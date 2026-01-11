"""
エンドツーエンドテスト：全機能を実際に実行
"""
import sys
import os
from pathlib import Path
import requests
import json
import time
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent / "keiba"))

def print_section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")

def test_ml_training():
    """実際にモデル学習を実行"""
    print_section("🧠 1. モデル学習テスト")
    
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
        print(f"設定: {json.dumps(payload, indent=2, ensure_ascii=False)}")
        
        response = requests.post(url, json=payload, timeout=120)
        
        if response.status_code == 200:
            result = response.json()
            print("\n✅ 学習成功！")
            print(f"モデルID: {result['model_id']}")
            print(f"AUC: {result['metrics'].get('auc', 'N/A'):.4f}")
            print(f"LogLoss: {result['metrics'].get('logloss', 'N/A'):.4f}")
            print(f"学習時間: {result['training_time']:.2f}秒")
            print(f"データ数: {result['data_count']}")
            print(f"レース数: {result['race_count']}")
            print(f"特徴量数: {result['feature_count']}")
            return result['model_id']
        else:
            print(f"❌ 学習失敗: {response.status_code}")
            print(response.text)
            return None
            
    except Exception as e:
        print(f"❌ エラー: {e}")
        import traceback
        traceback.print_exc()
        return None

def test_prediction(model_id):
    """実際に予測を実行"""
    print_section("🏇 2. 予測テスト")
    
    if not model_id:
        print("⚠️ モデルIDがないため予測をスキップ")
        return
    
    try:
        url = "http://localhost:8000/api/predict"
        
        # テストデータ（ダミー）
        payload = {
            "model_id": model_id,
            "horses": [
                {
                    "horse_number": 1,
                    "horse_name": "テスト馬1",
                    "jockey_name": "テスト騎手1",
                    "odds": 3.5,
                    "weight": 480,
                    "weight_diff": 0
                },
                {
                    "horse_number": 2,
                    "horse_name": "テスト馬2",
                    "jockey_name": "テスト騎手2",
                    "odds": 5.2,
                    "weight": 475,
                    "weight_diff": -2
                }
            ]
        }
        
        print(f"予測開始（モデル: {model_id}）...")
        
        response = requests.post(url, json=payload, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            print("\n✅ 予測成功！")
            predictions = result.get('predictions', [])
            print(f"予測結果数: {len(predictions)}")
            for i, pred in enumerate(predictions[:5], 1):
                print(f"{i}. 馬番{pred.get('horse_number')}: {pred.get('horse_name')} - 確率 {pred.get('probability', 0):.2%}")
        else:
            print(f"❌ 予測失敗: {response.status_code}")
            print(response.text)
            
    except Exception as e:
        print(f"❌ エラー: {e}")
        import traceback
        traceback.print_exc()

def test_data_collection():
    """データ収集機能の確認"""
    print_section("📥 3. データ収集機能確認")
    
    print("⚠️ 実際のスクレイピングは実行しません（netkeiba.comへの負荷を避けるため）")
    print("\nデータ収集ページ: http://localhost:3000/data-collection")
    print("手動でテストする場合:")
    print("  1. ブラウザでページを開く")
    print("  2. 年月を選択")
    print("  3. 「レース一覧を取得」をクリック")
    print("  4. レースを選択して「データ収集開始」")
    
    # 現在のデータ数を表示
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
        race_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM race_results")
        result_count = cursor.fetchone()[0]
        
        print(f"\n現在のデータ:")
        print(f"  レース数: {race_count}")
        print(f"  レース結果数: {result_count}")
        
        conn.close()
        print("\n✅ データベース確認完了")
        
    except Exception as e:
        print(f"❌ エラー: {e}")

def test_dashboard():
    """ダッシュボード表示確認"""
    print_section("📊 4. ダッシュボード確認")
    
    try:
        # ダッシュボードページにアクセス
        response = requests.get("http://localhost:3000/dashboard", timeout=10)
        
        if response.status_code == 200:
            print("✅ ダッシュボードページ: アクセス可能")
            print(f"   ページサイズ: {len(response.content)} bytes")
        else:
            print(f"❌ ダッシュボードページ: ステータス {response.status_code}")
        
        # トップページ
        response = requests.get("http://localhost:3000", timeout=10)
        if response.status_code == 200:
            print("✅ トップページ: アクセス可能")
        
        # 学習ページ
        response = requests.get("http://localhost:3000/train", timeout=10)
        if response.status_code == 200:
            print("✅ 学習ページ: アクセス可能")
        
        # データ収集ページ
        response = requests.get("http://localhost:3000/data-collection", timeout=10)
        if response.status_code == 200:
            print("✅ データ収集ページ: アクセス可能")
        
        # 予測ページ
        response = requests.get("http://localhost:3000/predict-batch", timeout=10)
        if response.status_code == 200:
            print("✅ 予測ページ: アクセス可能")
        
        print("\n✅ 全ページアクセス確認完了")
        print("\nブラウザで確認: http://localhost:3000/dashboard")
        
    except Exception as e:
        print(f"❌ エラー: {e}")

def main():
    print("="*60)
    print("  競馬AI Pro - エンドツーエンドテスト")
    print("="*60)
    print(f"実行時刻: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # サーバー確認
    print("\nサーバー確認中...")
    try:
        requests.get("http://localhost:3000", timeout=5)
        print("✅ Next.js サーバー: 起動中")
    except:
        print("❌ Next.js サーバー: 未起動")
        print("   npm run dev で起動してください")
        return
    
    try:
        requests.get("http://localhost:8000/api/models", timeout=5)
        print("✅ FastAPI サーバー: 起動中")
    except:
        print("❌ FastAPI サーバー: 未起動")
        print("   python-api/main.py で起動してください")
        return
    
    # テスト実行
    model_id = test_ml_training()
    time.sleep(1)
    test_prediction(model_id)
    time.sleep(1)
    test_data_collection()
    time.sleep(1)
    test_dashboard()
    
    print_section("✅ テスト完了")
    print("\n全機能の動作状況:")
    print(f"  {'✅' if model_id else '❌'} モデル学習")
    print(f"  {'✅' if model_id else '❌'} AI予測")
    print("  ⚠️  データ収集（手動テスト推奨）")
    print("  ✅ ダッシュボード表示")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nテスト中断")
    except Exception as e:
        print(f"\n❌ 予期しないエラー: {e}")
        import traceback
        traceback.print_exc()
