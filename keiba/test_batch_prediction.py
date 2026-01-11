"""
バッチ予測機能のテスト
"""
from pathlib import Path
from datetime import datetime, timedelta
import sys
import joblib

# パスを追加
sys.path.insert(0, str(Path(__file__).parent))

from keiba_ai.config import load_config
from keiba_ai.netkeiba.client import NetkeibaClient
from keiba_ai.pipeline_daily import create_prediction_features

def test_batch_prediction():
    print("=" * 80)
    print("バッチ予測機能テスト")
    print("=" * 80)
    
    # 設定読み込み
    cfg = load_config("config.yaml")
    print(f"\n✅ 設定ファイル読み込み完了")
    
    # モデル読み込み
    model_path = cfg.storage.models_dir / "model_latest.joblib"
    if not model_path.exists():
        print(f"\n❌ モデルファイルが見つかりません: {model_path}")
        return False
    
    try:
        model_bundle = joblib.load(model_path)
        print(f"✅ モデル読み込み完了: {model_path}")
        print(f"   - 特徴量数: {len(model_bundle.get('feature_cols_num', [])) + len(model_bundle.get('feature_cols_cat', []))}")
    except Exception as e:
        print(f"\n❌ モデル読み込みエラー: {e}")
        return False
    
    # レースID取得
    print(f"\n📡 レースID取得テスト")
    client = NetkeibaClient(cfg.netkeiba, cfg.storage)
    
    # 過去の日付でテスト（2024年12月21日）
    test_date = "20241221"
    print(f"   テスト日付: {test_date}")
    
    try:
        race_ids = client.fetch_race_list_by_date(test_date, use_cache=True)
        if race_ids:
            print(f"   ✅ {len(race_ids)}レース取得")
            # 最初の3件を表示
            for i, rid in enumerate(race_ids[:3]):
                print(f"      {i+1}. {rid}")
        else:
            print(f"   ⚠️ レースが見つかりませんでした")
            # 手動で生成
            race_ids = [f"{test_date}0501"]
    except Exception as e:
        print(f"   ❌ エラー: {e}")
        race_ids = [f"{test_date}0501"]
    
    # 1つのレースで予測テスト
    print(f"\n🔮 予測実行テスト")
    test_race_id = race_ids[0] if race_ids else f"{test_date}0501"
    print(f"   テストレース: {test_race_id}")
    
    try:
        # 特徴量作成
        print(f"   📊 特徴量作成中...")
        pfc = create_prediction_features(test_race_id, cfg)
        features = pfc.features
        
        if features is None or features.empty:
            print(f"   ❌ 特徴量が空です")
            return False
        
        print(f"   ✅ 特徴量作成完了")
        print(f"      - 行数: {len(features)}")
        print(f"      - 列数: {len(features.columns)}")
        print(f"      - カラム: {list(features.columns)[:5]}...")
        
        # モデルから必要な特徴量を取得
        feature_cols_num = model_bundle.get("feature_cols_num", [])
        feature_cols_cat = model_bundle.get("feature_cols_cat", [])
        feature_cols = feature_cols_num + feature_cols_cat
        
        print(f"\n   🔍 特徴量チェック")
        print(f"      - モデル必要特徴量: {len(feature_cols)}")
        
        # 不足している特徴量をチェック
        missing_cols = [col for col in feature_cols if col not in features.columns]
        if missing_cols:
            print(f"   ⚠️ 不足している特徴量: {len(missing_cols)}")
            for col in missing_cols[:5]:
                print(f"      - {col}")
            if len(missing_cols) > 5:
                print(f"      ... 他{len(missing_cols) - 5}件")
            return False
        
        print(f"   ✅ 全ての必要特徴量が揃っています")
        
        # 予測実行
        print(f"\n   🎯 予測実行中...")
        X = features[feature_cols].copy()
        model = model_bundle['model']
        
        if hasattr(model, 'predict_proba'):
            pred_win = model.predict_proba(X)[:, 1]
        else:
            pred_win = model.predict(X)
        
        print(f"   ✅ 予測完了")
        print(f"      - 予測頭数: {len(pred_win)}")
        print(f"      - 予測値範囲: {pred_win.min():.4f} 〜 {pred_win.max():.4f}")
        
        # 上位3頭を表示
        import pandas as pd
        horse_no_col = 'umaban' if 'umaban' in features.columns else 'horse_no'
        predictions = pd.DataFrame({
            'umaban': features[horse_no_col],
            'pred_win': pred_win
        })
        predictions = predictions.sort_values('pred_win', ascending=False)
        
        print(f"\n   📊 予測結果（上位3頭）:")
        for i, (idx, row) in enumerate(predictions.head(3).iterrows(), 1):
            print(f"      {i}位: {int(row['umaban'])}番 ({row['pred_win']:.2%})")
        
        return True
        
    except Exception as e:
        print(f"   ❌ 予測エラー: {e}")
        import traceback
        print("\n詳細:")
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_batch_prediction()
    
    print(f"\n" + "=" * 80)
    if success:
        print(f"✅ テスト成功: 予測処理は正常に動作しています")
    else:
        print(f"❌ テスト失敗: 予測処理に問題があります")
    print("=" * 80)
