"""Runtime facade for training package."""

from __future__ import annotations

from ..optuna_runtime import (
    auto_cap_trials_from_history,
    build_optuna_controls,
    get_default_mode_presets,
    get_mode_cfg,
    merge_mode_presets_from_yaml,
    resolve_runtime_mode,
)

__all__ = [
    "auto_cap_trials_from_history",
    "build_optuna_controls",
    "get_default_mode_presets",
    "get_mode_cfg",
    "merge_mode_presets_from_yaml",
    "resolve_runtime_mode",
]
