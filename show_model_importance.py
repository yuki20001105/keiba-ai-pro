import sys, joblib, json, os, glob
import numpy as np
sys.path.insert(0, 'keiba')
sys.path.insert(0, 'python-api')

# 最新モデルを直接ロード
model_dir = 'python-api/models'
models = sorted(glob.glob(f'{model_dir}/*.joblib'), key=os.path.getmtime, reverse=True)
print('Available models:')
for m in models[:5]:
    print(' ', os.path.basename(m))

# speed_deviationモデルを優先
target = next((m for m in models if 'speed_deviation' in m), models[0] if models else None)
if not target:
    print('No model found')
    exit()

print(f'\nLoading: {os.path.basename(target)}')
data = joblib.load(target)
booster = data.get('model') or data.get('booster') or data

# feature importance
if hasattr(booster, 'feature_importance'):
    gains = booster.feature_importance(importance_type='gain')
    splits = booster.feature_importance(importance_type='split')
    names = booster.feature_name()
    total_gain = gains.sum() or 1
    total_split = splits.sum() or 1
    
    idx = np.argsort(gains)[::-1][:35]
    print(f'\n{"Rank":>4}  {"Feature":<45} {"Gain%":>7}  {"Split%":>7}')
    print('-'*70)
    for rank, i in enumerate(idx, 1):
        g = gains[i] / total_gain * 100
        s = splits[i] / total_split * 100
        print(f"{rank:>4}. {names[i]:<45} {g:>6.2f}%  {s:>6.2f}%")
elif hasattr(booster, 'feature_importances_'):
    print(booster.feature_importances_)
else:
    print('Keys:', list(data.keys()) if isinstance(data, dict) else type(data))
