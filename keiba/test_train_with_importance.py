"""
学習をテストして特徴量重要度を確認
"""
from pathlib import Path
from keiba_ai.train import train

print("学習を開始...")
model_path = train(Path("config.yaml"))
print(f"\n✅ 学習完了: {model_path}")

# 特徴量重要度を表示
import joblib
bundle = joblib.load(model_path)

print("\n" + "=" * 60)
print("特徴量重要度 Top 20")
print("=" * 60)

if "feature_importance" in bundle:
    importance_df = bundle["feature_importance"]
    
    for idx, row in importance_df.head(20).iterrows():
        feature = row["feature"]
        coef = row["coefficient"]
        abs_coef = row["abs_coefficient"]
        direction = "勝ち↑" if coef > 0 else "負け↑"
        print(f"{idx+1:2d}. {feature:30s} 係数={coef:8.4f} 重要度={abs_coef:.4f} [{direction}]")
else:
    print("⚠️ 特徴量重要度が含まれていません")

print("\n" + "=" * 60)
print(f"AUC: {bundle['metrics']['auc']:.4f}")
print(f"Log Loss: {bundle['metrics']['logloss']:.4f}")
print("=" * 60)
