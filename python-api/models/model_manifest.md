# Model Manifest

This file documents tracked model artifacts used by the runtime.

## Policy

- Some `.joblib` files are currently tracked because the runtime resolves active models from `python-api/models`.
- Generated or experimental model artifacts should not be committed unless they are required for local runtime, demo, validation, or release behavior.
- Do not remove the tracked `.joblib` files until the runtime is able to load models from an external artifact source or a registry.

## Tracked Models

| File | Size (MB) | Last Write Time | SHA256 | Purpose | Runtime Reference | Regenerable | Keep in Git | Notes |
|---|---:|---|---|---|---|---|---|---|
| `model_speed_deviation_lightgbm_20160101_20260322_20260418_1928.joblib` | 3.47 | 2026-04-18 23:20:27 | `9577C7634B6B44AD2CFAD94FC565A6EE712CCC10E6D2D5259BA5B13028E7E64E` | Runtime model artifact kept for application compatibility | `python-api/app_config.py:get_latest_model()` and `python-api/routers/models_mgmt.py` | Yes, from training pipeline | Yes, for now | Candidate for external artifact storage |
| `model_speed_deviation_lightgbm_20240101_20260201_20260419_2024.joblib` | 0.48 | 2026-07-05 02:34:40 | `8378FAE9FB5AE1B84F51B833D35B6B36EDCE25351635EBB692BA97757C1EB37D` | Runtime model artifact kept for application compatibility | `python-api/app_config.py:get_latest_model()` and `python-api/routers/models_mgmt.py` | Yes, from training pipeline | Yes, for now | Candidate for external artifact storage |

## Runtime Notes

- `python-api/app_config.py` selects the latest `model_*.joblib` / `model_win_*.joblib` artifact from `python-api/models`.
- `python-api/routers/models_mgmt.py` expects model files to exist locally when serving model-management operations.
- `python-api/routers/predict.py` also loads models through the same runtime model resolution path.

## Future Direction

Long term, large model artifacts should move to one of the following:

- GitHub Release artifacts
- Model registry
- Cloud/object storage
- Local download script

Until then, only runtime-required model artifacts should remain tracked.
