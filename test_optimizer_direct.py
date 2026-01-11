"""
最小限のテスト: optuna_optimizer.optimize() が呼ばれているか確認
"""
import sys
sys.path.insert(0, r"C:\Users\yuki2\Documents\ws\keiba-ai-pro\keiba")

from keiba_ai.optuna_optimizer import OptunaLightGBMOptimizer
import numpy as np

print("\n=== OptunaLightGBMOptimizer 直接テスト ===\n")

# テストデータ
X = np.random.rand(150, 22)
y = np.random.randint(0, 2, 150)

print(f"データ: X={X.shape}, y={y.shape}")
print(f"試行回数: 3")
print(f"CVフォールド: 2")
print("\n" + "="*70)

# Optimizer作成
opt = OptunaLightGBMOptimizer(
    n_trials=3,
    cv_folds=2,
    random_state=42,
    timeout=60,
    show_progress=True
)

print("✓ Optimizer 初期化完了\n")
print("optimize()メソッド呼び出し開始...\n")

# 最適化実行
try:
    best_params, best_score = opt.optimize(X, y, categorical_features=[20, 21])
    print(f"\n✓ 最適化完了")
    print(f"ベストスコア: {best_score:.4f}")
    print(f"\nベストパラメータ:")
    for key, value in best_params.items():
        print(f"  {key}: {value}")
except Exception as e:
    print(f"\n❌ エラー: {e}")
    import traceback
    traceback.print_exc()
