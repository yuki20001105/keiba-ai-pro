"""
予測システムの動作確認スクリプト
"""
import sys
from pathlib import Path

# パス追加
sys.path.insert(0, str(Path(__file__).parent / "keiba"))

from keiba_ai.db import connect

def test_prediction_system():
    """予測システムの動作確認"""
    print("=" * 70)
    print("予測システム動作確認")
    print("=" * 70)
    
    # 1. データベース確認
    print("\n[1/3] データベース確認...")
    db_path = Path("keiba/data/keiba.db")
    
    if db_path.exists():
        print(f"   ✅ データベース存在: {db_path}")
        print(f"   サイズ: {db_path.stat().st_size / 1024 / 1024:.2f} MB")
        
        con = connect(db_path)
        cursor = con.cursor()
        
        # テーブル一覧
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        print(f"\n   📊 テーブル数: {len(tables)}")
        
        # 各テーブルのレコード数
        for table in tables:
            table_name = table[0]
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            count = cursor.fetchone()[0]
            print(f"      {table_name:20s}: {count:5d} レコード")
        
        con.close()
    else:
        print(f"   ❌ データベースが見つかりません: {db_path}")
        return False
    
    # 2. モデルファイル確認
    print("\n[2/3] モデルファイル確認...")
    models_dir = Path("keiba/models")
    
    if models_dir.exists():
        print(f"   ✅ モデルディレクトリ存在")
        model_files = list(models_dir.glob("*.pkl"))
        print(f"   モデルファイル数: {len(model_files)}")
        
        if model_files:
            print("\n   最近のモデル:")
            for mf in sorted(model_files, key=lambda x: x.stat().st_mtime, reverse=True)[:3]:
                size_mb = mf.stat().st_size / 1024 / 1024
                print(f"      - {mf.name} ({size_mb:.2f} MB)")
        else:
            print("   ⚠️ モデルファイルが見つかりません")
            print("   → 「2_学習」ページでモデルを学習してください")
    else:
        print("   ❌ モデルディレクトリが見つかりません")
        return False
    
    # 3. 設定ファイル確認
    print("\n[3/3] 設定ファイル確認...")
    config_path = Path("keiba/config.yaml")
    
    if config_path.exists():
        print(f"   ✅ 設定ファイル存在: {config_path}")
        
        # 設定内容の簡易確認
        try:
            from keiba_ai.config import load_config
            cfg = load_config(config_path)
            print(f"   Target: {cfg.training.target}")
            print(f"   Random Seed: {cfg.training.random_seed}")
        except Exception as e:
            print(f"   ⚠️ 設定ファイルの読み込みエラー: {e}")
    else:
        print(f"   ❌ 設定ファイルが見つかりません: {config_path}")
        return False
    
    # 4. 予測機能のインポート確認
    print("\n[4/4] 予測機能のインポート確認...")
    try:
        from keiba_ai.train import train
        from keiba_ai.pipeline_daily import create_prediction_features
        print("   ✅ train関数インポート成功")
        print("   ✅ create_prediction_features関数インポート成功")
    except ImportError as e:
        print(f"   ❌ インポートエラー: {e}")
        return False
    
    # 総合判定
    print("\n" + "=" * 70)
    print("✅ 予測システムの動作確認完了")
    print("=" * 70)
    
    # 使用方法の表示
    print("\n📝 予測システムの使用方法:")
    print("\n1. データ取得（データがない場合）:")
    print("   python keiba/register_to_db.py --race-ids 202401010101")
    
    print("\n2. モデル学習（モデルがない場合）:")
    print("   python keiba/keiba_ai/train.py keiba/config.yaml")
    
    print("\n3. FastAPI起動:")
    print("   cd python-api")
    print("   $env:PYTHONPATH=\"C:\\Users\\yuki2\\Documents\\ws\\keiba-ai-pro\"")
    print("   uvicorn main:app --host 0.0.0.0 --port 8000")
    
    print("\n4. 予測実行（FastAPI経由）:")
    print("   POST http://localhost:8000/api/predict")
    
    print("\n" + "=" * 70)
    return True


if __name__ == "__main__":
    try:
        success = test_prediction_system()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ エラー発生: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
