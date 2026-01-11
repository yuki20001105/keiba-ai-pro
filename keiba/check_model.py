"""モデルファイルの内容を確認"""
import joblib
from pathlib import Path

model_dir = Path("data/models")
model_files = list(model_dir.glob("*.joblib"))

if not model_files:
    print("❌ モデルファイルが見つかりません")
else:
    latest_model = max(model_files, key=lambda p: p.stat().st_mtime)
    print(f"最新のモデルファイル: {latest_model.name}")
    print("=" * 60)
    
    bundle = joblib.load(latest_model)
    print(f"bundleの型: {type(bundle)}")
    
    if isinstance(bundle, dict):
        print(f"bundleのキー: {list(bundle.keys())}")
        print()
        for key, value in bundle.items():
            print(f"  {key}: {type(value)}")
    else:
        print(f"⚠️ bundleは辞書ではありません: {type(bundle)}")
        print(f"bundleの内容: {bundle}")
