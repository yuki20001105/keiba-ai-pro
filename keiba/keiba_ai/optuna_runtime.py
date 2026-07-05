from __future__ import annotations

from pathlib import Path


def get_default_mode_presets() -> dict:
    return {
        "fast": {
            "n_trials": 10,
            "n_splits": 3,
            "boosting": "gbdt",
            "num_boost_round": 200,
        },
        "audit": {
            "n_trials": 20,
            "n_splits": 3,
            "boosting": "gbdt",
            "num_boost_round": 150,
        },
        "prod": {
            "n_trials": 30,
            "n_splits": 5,
            "boosting": "gbdt",
            "num_boost_round": 500,
        },
    }


def merge_mode_presets_from_yaml(mode_presets: dict, yaml_path: Path, yaml_module=None) -> dict:
    merged = dict(mode_presets)
    if yaml_module is None or not yaml_path.exists():
        return merged
    try:
        loaded = yaml_module.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return merged
    if not isinstance(loaded, dict):
        return merged
    for k, v in loaded.items():
        if k not in merged or not isinstance(v, dict):
            continue
        base = dict(merged[k])
        base.update(v)
        merged[k] = base
    return merged
