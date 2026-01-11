"""
エンドツーエンドテスト準備確認
"""
import sys
sys.path.insert(0, r'C:\Users\yuki2\Documents\ws\keiba-ai-pro\keiba')

from pathlib import Path
from keiba_ai.db import connect, load_training_frame
from keiba_ai.feature_engineering import add_derived_features
from keiba_ai.lightgbm_feature_optimizer import prepare_for_lightgbm_ultimate

print("\n" + "="*60)
print("【エンドツーエンド実行前チェック】")
print("="*60)

# 1. データベース確認
print("\n1. データベース確認:")
db_path = Path(r'C:\Users\yuki2\Documents\ws\keiba-ai-pro\keiba\data\keiba.db')
print(f"   DB存在: {'✓' if db_path.exists() else '✗'} {db_path}")

if db_path.exists():
    try:
        conn = connect(db_path)
        df = load_training_frame(conn)
        conn.close()
        
        print(f"   データ数: {len(df)}行")
        print(f"   レース数: {df['race_id'].nunique() if 'race_id' in df.columns else 0}レース")
        print(f"   カラム数: {len(df.columns)}列")
        
        if len(df) > 0:
            print(f"   ✓ 学習可能なデータあり")
            
            # 派生特徴量を追加してみる
            print("\n2. 派生特徴量生成テスト:")
            df_with_features = add_derived_features(df.head(10), full_history_df=df)
            print(f"   元のカラム: {len(df.columns)}列")
            print(f"   派生後: {len(df_with_features.columns)}列")
            print(f"   追加された特徴量: {len(df_with_features.columns) - len(df.columns)}個")
            print(f"   ✓ 特徴量生成OK")
            
            # 最適化テスト
            print("\n3. LightGBM最適化テスト:")
            try:
                df_opt, optimizer, cat_features = prepare_for_lightgbm_ultimate(
                    df_with_features,
                    target_col=None,
                    is_training=True
                )
                print(f"   最適化後: {len(df_opt.columns)}列")
                print(f"   カテゴリカル特徴量: {len(cat_features)}個")
                print(f"   ✓ 最適化OK")
            except Exception as e:
                print(f"   ✗ 最適化エラー: {e}")
            
        else:
            print(f"   ✗ データが空です")
            
    except Exception as e:
        print(f"   ✗ データベースエラー: {e}")
else:
    print(f"   ✗ データベースが存在しません")

# 4. FastAPI確認
print("\n4. FastAPI接続確認:")
try:
    import requests
    response = requests.get("http://localhost:8000/health", timeout=3)
    if response.status_code == 200:
        print(f"   ✓ FastAPI起動中")
        data = response.json()
        print(f"   Status: {data.get('status')}")
    else:
        print(f"   ✗ FastAPI応答異常: {response.status_code}")
except requests.exceptions.ConnectionError:
    print(f"   ✗ FastAPIが起動していません")
    print(f"   → 手動で起動してください:")
    print(f"      cd python-api")
    print(f"      $env:PYTHONPATH='C:\\Users\\yuki2\\Documents\\ws\\keiba-ai-pro'")
    print(f"      python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload")
except Exception as e:
    print(f"   ✗ エラー: {e}")

print("\n" + "="*60)
print("【チェック完了】")
print("="*60)
print("\nFastAPIが起動していれば、次のコマンドでテスト実行:")
print("  python test_optimized_api.py")
print()
