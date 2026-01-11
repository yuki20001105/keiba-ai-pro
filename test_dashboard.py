"""
ダッシュボード機能の包括的テスト
すべてのAPI機能を順次テストする
"""
import sys
import os
from pathlib import Path
import sqlite3
import requests
import json
from datetime import datetime

# カラー出力用
def print_success(msg):
    print(f"✅ {msg}")

def print_error(msg):
    print(f"❌ {msg}")

def print_info(msg):
    print(f"ℹ️  {msg}")

def print_section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")

# 親ディレクトリのkeibaモジュールをインポート
sys.path.insert(0, str(Path(__file__).parent / "keiba"))

def test_servers():
    """サーバーの動作確認"""
    print_section("1. サーバー動作確認")
    
    # Next.js
    try:
        response = requests.get("http://localhost:3000", timeout=5)
        print_success(f"Next.js サーバー起動中 (ステータス: {response.status_code})")
    except Exception as e:
        print_error(f"Next.js サーバー未起動: {e}")
    
    # FastAPI
    try:
        response = requests.get("http://localhost:8000/api/models", timeout=5)
        print_success(f"FastAPI サーバー起動中 (ステータス: {response.status_code})")
        models = response.json()
        if isinstance(models, list):
            print_info(f"保存済みモデル数: {len(models)}")
            for model in models:
                if isinstance(model, dict):
                    print(f"  - {model.get('model_id', 'N/A')}: {model.get('model_type', 'N/A')} (AUC: {model.get('metrics', {}).get('auc', 'N/A')})")
        else:
            print_info(f"モデル情報: {models}")
    except Exception as e:
        print_error(f"FastAPI サーバー未起動またはエラー: {e}")

def test_database():
    """データベースの動作確認"""
    print_section("2. データベース接続とデータ確認")
    
    try:
        from keiba_ai.config import load_config
        config = load_config(str(Path(__file__).parent / "keiba" / "config.yaml"))
        
        # AppConfigオブジェクトから属性としてアクセス
        db_path = str(config.storage.sqlite_path)
        
        # 絶対パスに変換
        if not os.path.isabs(db_path):
            db_path = os.path.join(Path(__file__).parent / "keiba", db_path)
        
        print_info(f"データベースパス: {db_path}")
        
        if not os.path.exists(db_path):
            print_error(f"データベースファイルが存在しません: {db_path}")
            return
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # テーブル一覧を取得
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = cursor.fetchall()
        print_success(f"データベース接続成功 (テーブル数: {len(tables)})")
        
        # 各テーブルのレコード数を確認
        important_tables = ['races', 'race_results', 'horses', 'predictions', 'bets', 'bank_records']
        for table in tables:
            table_name = table[0]
            if table_name in important_tables:
                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                count = cursor.fetchone()[0]
                print_info(f"{table_name}: {count} レコード")
        
        # レースデータの確認
        cursor.execute("SELECT COUNT(DISTINCT race_id) FROM races")
        race_count = cursor.fetchone()[0]
        print_success(f"レース数: {race_count} レース")
        
        # テーブルスキーマを確認して最新レース日付を取得
        cursor.execute("PRAGMA table_info(races)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'race_date' in columns:
            cursor.execute("SELECT MAX(race_date) FROM races")
            latest_date = cursor.fetchone()[0]
            if latest_date:
                print_info(f"最新レース日付: {latest_date}")
        elif 'date' in columns:
            cursor.execute("SELECT MAX(date) FROM races")
            latest_date = cursor.fetchone()[0]
            if latest_date:
                print_info(f"最新レース日付: {latest_date}")
        
        conn.close()
        
    except Exception as e:
        print_error(f"データベースエラー: {e}")
        import traceback
        traceback.print_exc()

def test_ml_api():
    """機械学習APIの動作確認"""
    print_section("3. 機械学習API テスト")
    
    base_url = "http://localhost:8000"
    
    # モデルリスト取得
    try:
        response = requests.get(f"{base_url}/api/models", timeout=5)
        print_success(f"GET /api/models: {response.status_code}")
        models = response.json()
        
        if len(models) > 0:
            print_info(f"保存済みモデル: {len(models)} 件")
            
            # 最新モデルで予測テスト
            latest_model = models[0]
            print_info(f"最新モデル: {latest_model['model_id']}")
            
            # 予測リクエストのサンプル
            predict_data = {
                "model_id": latest_model['model_id'],
                "horses": [
                    {
                        "horse_number": 1,
                        "horse_name": "テスト馬1",
                        "jockey_name": "テスト騎手1",
                        "odds": 3.5
                    }
                ]
            }
            
            try:
                response = requests.post(f"{base_url}/api/predict", json=predict_data, timeout=10)
                if response.status_code == 200:
                    print_success(f"POST /api/predict: 予測成功")
                    result = response.json()
                    print_info(f"予測結果数: {len(result.get('predictions', []))}")
                else:
                    print_error(f"POST /api/predict: {response.status_code} - {response.text}")
            except Exception as e:
                print_error(f"予測APIエラー: {e}")
        else:
            print_info("保存済みモデルなし - 学習が必要です")
            
    except Exception as e:
        print_error(f"機械学習APIエラー: {e}")

def test_next_api():
    """Next.js APIの動作確認"""
    print_section("4. Next.js API テスト")
    
    base_url = "http://localhost:3000"
    
    # APIエンドポイントのテスト
    endpoints = [
        ("/api/races", "GET", "レース一覧"),
        ("/api/predictions", "GET", "予測一覧"),
        ("/api/bets", "GET", "賭け履歴"),
        ("/api/bank-records", "GET", "資金記録"),
    ]
    
    for endpoint, method, description in endpoints:
        try:
            response = requests.get(f"{base_url}{endpoint}", timeout=5)
            if response.status_code == 200:
                print_success(f"{method} {endpoint}: {description}")
                data = response.json()
                if isinstance(data, list):
                    print_info(f"  データ件数: {len(data)}")
            else:
                print_error(f"{method} {endpoint}: ステータス {response.status_code}")
        except Exception as e:
            print_error(f"{method} {endpoint}: {e}")

def test_training_flow():
    """学習フローのテスト（実際の学習は実行しない）"""
    print_section("5. 学習フロー確認")
    
    try:
        from keiba_ai.db import connect, load_training_frame
        from keiba_ai.config import load_config
        
        config = load_config(str(Path(__file__).parent / "keiba" / "config.yaml"))
        db_path = str(config.storage.sqlite_path)
        
        if not os.path.isabs(db_path):
            db_path = os.path.join(Path(__file__).parent / "keiba", db_path)
        
        print_info("学習データの読み込み確認...")
        
        # SQLite接続文字列を作成
        import sqlite3
        conn = sqlite3.connect(db_path)
        df = load_training_frame(conn)
        
        if df is not None and len(df) > 0:
            print_success(f"学習データ読み込み成功: {len(df)} レコード")
            print_info(f"ユニークなレース数: {df['race_id'].nunique()}")
            print_info(f"カラム数: {len(df.columns)}")
            
            # 必要な最小レコード数チェック
            min_records = 100
            if len(df) >= min_records:
                print_success(f"十分な学習データあり (>= {min_records})")
            else:
                print_error(f"学習データ不足: {len(df)} < {min_records}")
                print_info("データ収集ページでより多くのレースデータを取得してください")
        else:
            print_error("学習データなし - データ収集が必要です")
            
    except Exception as e:
        print_error(f"学習フローエラー: {e}")
        import traceback
        traceback.print_exc()

def print_summary():
    """テスト結果のサマリー"""
    print_section("📊 テスト完了")
    print("\n次のステップ:")
    print("1. データが不足している場合:")
    print("   → http://localhost:3000/data-collection でレースデータを収集")
    print("\n2. モデルを学習する場合:")
    print("   → http://localhost:3000/train でモデルを学習")
    print("\n3. 予測を実行する場合:")
    print("   → http://localhost:3000/predict-batch で一括予測")
    print("\n4. ダッシュボードで統計を確認:")
    print("   → http://localhost:3000/dashboard")
    print()

if __name__ == "__main__":
    print("=" * 60)
    print("  競馬AI Pro - ダッシュボード機能テスト")
    print("=" * 60)
    print(f"実行時刻: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    try:
        test_servers()
        test_database()
        test_ml_api()
        test_next_api()
        test_training_flow()
        print_summary()
    except KeyboardInterrupt:
        print("\n\nテスト中断")
    except Exception as e:
        print_error(f"予期しないエラー: {e}")
        import traceback
        traceback.print_exc()
