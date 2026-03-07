"""Debug script to find where regen_pipeline_output.py fails."""
import sys, os, logging, traceback
from pathlib import Path
import warnings; warnings.filterwarnings("ignore")

logging.disable(logging.CRITICAL)

ROOT  = Path(__file__).parent.parent
PYAPI = ROOT / "python-api"
KEIBA = ROOT / "keiba"
sys.path.insert(0, str(PYAPI))
sys.path.insert(0, str(KEIBA))
os.chdir(str(PYAPI))

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

print("=== Step A: imports ===", flush=True)
import pandas as pd
import numpy as np
import joblib
print("  pandas/numpy/joblib OK", flush=True)

from app_config import ULTIMATE_DB, MODELS_DIR, get_latest_model, load_model_bundle
print(f"  app_config OK. MODELS_DIR={MODELS_DIR}", flush=True)

from keiba_ai.db_ultimate_loader import load_ultimate_training_frame
print("  db_ultimate_loader OK", flush=True)

from keiba_ai.feature_engineering import add_derived_features
print("  feature_engineering OK", flush=True)

from keiba_ai.ultimate_features import UltimateFeatureCalculator
print("  ultimate_features OK", flush=True)

print("=== Step B: model load ===", flush=True)
win_models = sorted(MODELS_DIR.glob("model_win_*_ultimate.joblib"),
                    key=lambda p: p.stat().st_mtime, reverse=True)
print(f"  found {len(win_models)} models", flush=True)
model_path = win_models[0]
print(f"  loading {model_path.name}", flush=True)

try:
    bundle = load_model_bundle(model_path)
    print(f"  bundle keys: {list(bundle.keys())}", flush=True)
except Exception as e:
    print(f"  FAIL: {e}", flush=True); traceback.print_exc(); sys.exit(1)

model     = bundle["model"]
optimizer = bundle["optimizer"]
feat_cols = bundle["feature_columns"]
print(f"  feat_cols: {len(feat_cols)}", flush=True)

print("=== Step C: DB load ===", flush=True)
try:
    df_raw = load_ultimate_training_frame(ULTIMATE_DB)
    print(f"  {len(df_raw):,} rows x {len(df_raw.columns)} cols", flush=True)
except Exception as e:
    print(f"  FAIL: {e}", flush=True); traceback.print_exc(); sys.exit(1)

print("=== Step D: add_derived_features ===", flush=True)
try:
    df_fe = add_derived_features(df_raw.copy(), full_history_df=df_raw.copy())
    print(f"  {len(df_fe.columns)} cols", flush=True)
except Exception as e:
    print(f"  FAIL: {e}", flush=True); traceback.print_exc(); sys.exit(1)

print("=== Step E: UltimateFeatureCalculator ===", flush=True)
try:
    calc  = UltimateFeatureCalculator(str(ULTIMATE_DB))
    df_ult = calc.add_ultimate_features(df_fe.copy())
    df_ult = df_ult.loc[:, ~df_ult.columns.duplicated()]
    print(f"  {len(df_ult.columns)} cols", flush=True)
except Exception as e:
    print(f"  FAIL: {e}", flush=True); traceback.print_exc(); sys.exit(1)

print("=== Step F: optimizer.transform ===", flush=True)
try:
    df_opt = optimizer.transform(df_ult.copy())
    avail  = [c for c in feat_cols if c in df_opt.columns]
    print(f"  {len(avail)}/{len(feat_cols)} features available", flush=True)
except Exception as e:
    print(f"  FAIL: {e}", flush=True); traceback.print_exc(); sys.exit(1)

print("=== ALL STEPS OK ===", flush=True)
