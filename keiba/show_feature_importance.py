"""
モデルの特徴量重要度を確認するスクリプト
"""
from pathlib import Path
import joblib
import pandas as pd
import sys

def show_feature_importance(model_path: str):
    """モデルの特徴量重要度を表示"""
    bundle = joblib.load(model_path)
    
    print("=" * 80)
    print(f"モデル: {Path(model_path).name}")
    print("=" * 80)
    
    # メトリクス
    if "metrics" in bundle:
        metrics = bundle["metrics"]
        print(f"\n【検証メトリクス】")
        print(f"  AUC: {metrics['auc']:.4f}")
        print(f"  Log Loss: {metrics['logloss']:.4f}")
    
    # 特徴量重要度
    if "feature_importance" in bundle:
        importance_df = bundle["feature_importance"]
        
        print(f"\n【特徴量重要度 Top 30】")
        print("=" * 80)
        print(f"{'順位':<4} {'特徴量':<30} {'係数':>12} {'重要度':>12} {'影響'}")
        print("-" * 80)
        
        for idx, row in importance_df.head(30).iterrows():
            feature = row["feature"]
            coef = row["coefficient"]
            abs_coef = row["abs_coefficient"]
            
            # 影響の方向
            if coef > 0:
                direction = "🔵 勝ちやすさ↑"
            else:
                direction = "🔴 負けやすさ↑"
            
            print(f"{idx+1:<4} {feature:<30} {coef:>12.6f} {abs_coef:>12.6f} {direction}")
        
        print("=" * 80)
        
        # カテゴリ別の統計
        print(f"\n【カテゴリ別の特徴量数】")
        
        categories = {
            "オッズ・人気": ["entry_odds", "entry_popularity"],
            "馬番・枠": ["horse_no", "bracket"],
            "馬の属性": ["age", "sex", "handicap", "weight", "weight_diff"],
            "騎手": ["jockey_id"],
            "調教師": ["trainer_id"],
        }
        
        for category, prefixes in categories.items():
            count = sum(
                importance_df["feature"].str.startswith(tuple(prefixes)).sum()
                for prefix in prefixes
                if prefix in " ".join(importance_df["feature"].tolist())
            )
            print(f"  {category}: {count}件")
        
    else:
        print("\n⚠️ このモデルには特徴量重要度が含まれていません")
        print("新しいバージョンで再学習してください")
    
    print()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        # デフォルトで最新モデルを表示
        model_dir = Path("data/models")
        if model_dir.exists():
            model_files = sorted(model_dir.glob("model_win_*.joblib"), reverse=True)
            if model_files:
                model_path = str(model_files[0])
                print(f"最新モデルを表示: {model_path}\n")
            else:
                print("エラー: モデルファイルが見つかりません")
                print("使い方: python show_feature_importance.py [model_path.joblib]")
                sys.exit(1)
        else:
            print("エラー: data/models ディレクトリが見つかりません")
            sys.exit(1)
    else:
        model_path = sys.argv[1]
    
    show_feature_importance(model_path)
