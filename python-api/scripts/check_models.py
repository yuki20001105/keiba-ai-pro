"""現在のモデルバンドルからメトリクスを表示するユーティリティ。"""
import sys
import joblib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "keiba"))

models_dir = Path(__file__).parent.parent / "models"
models = sorted(models_dir.glob("*.joblib"), key=lambda p: p.stat().st_mtime, reverse=True)

print("=== 現在のモデル一覧 ===\n")
for m in models[:8]:
    try:
        b = joblib.load(m)
        mt = b.get("metrics", {})
        fc = len(b.get("feature_columns", []))
        print(f"[{m.name}]")
        print(f"  target       : {b.get('target', '?')}")
        print(f"  data_count   : {b.get('data_count', '?')}")
        print(f"  race_count   : {b.get('race_count', '?')}")
        print(f"  date_range   : {b.get('training_date_from', '?')} ~ {b.get('training_date_to', '?')}")
        print(f"  features     : {fc}")
        print(f"  AUC          : {mt.get('auc', 'N/A')}")
        print(f"  cv_auc_mean  : {mt.get('cv_auc_mean', 'N/A')}")
        print(f"  cv_auc_std   : {mt.get('cv_auc_std', 'N/A')}")
        print(f"  logloss      : {mt.get('logloss', 'N/A')}")
        print(f"  top1_accuracy: {mt.get('top1_accuracy', 'N/A')}")
        print(f"  temperature  : {mt.get('softmax_temperature', mt.get('temperature', 'N/A'))}")
        # 追加メトリクス
        for k, v in mt.items():
            if k not in ("auc", "cv_auc_mean", "cv_auc_std", "logloss", "top1_accuracy",
                         "softmax_temperature", "temperature"):
                print(f"  {k:<14}: {v}")
        print()
    except Exception as e:
        print(f"[{m.name}]  ERROR: {e}\n")
