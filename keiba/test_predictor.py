"""prediction.pyを直接テスト"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from keiba_ai.prediction import Predictor
from keiba_ai.extract_odds import RealtimeOdds

# ダミーのRealtimeOdds
realtime_odds = RealtimeOdds(race_id="202401010101")
realtime_odds.tansho = {1: 3.5, 2: 5.0, 3: 10.0}

print("Predictorを初期化中...")
try:
    predictor = Predictor(
        race_id="202401010101",
        realtime_odds=realtime_odds,
        model_dir="data/models",
        config_filepath="config.yaml"
    )
    print(f"✅ 初期化成功")
    print(f"  モデル: {type(predictor.model)}")
    print(f"  特徴量（数値）: {len(predictor.feature_cols_num)}個")
    print(f"  特徴量（カテゴリ）: {len(predictor.feature_cols_cat)}個")
except Exception as e:
    print(f"❌ エラー: {e}")
    import traceback
    traceback.print_exc()
