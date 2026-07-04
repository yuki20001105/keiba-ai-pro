"""Evaluation helpers for training outputs."""

from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, log_loss, mean_squared_error, roc_auc_score


def evaluate_predictions(task: str, y_true, y_pred) -> Dict[str, float]:
    """Evaluate model predictions for binary or regression tasks."""
    if task == "binary":
        return {
            "auc": round(float(roc_auc_score(y_true, y_pred)), 4),
            "log_loss": round(float(log_loss(y_true, y_pred)), 4),
            "acc": round(float(accuracy_score(y_true, (np.asarray(y_pred) > 0.5))), 4),
        }

    rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
    corr = float(pd.Series(y_pred).corr(pd.Series(y_true.values)))
    return {"rmse": round(rmse, 4), "corr": round(corr, 4)}
