"""Search-space facade for training package."""

from __future__ import annotations

from ..search_space import build_search_space, sample_optuna_lgb_params

__all__ = ["build_search_space", "sample_optuna_lgb_params"]
