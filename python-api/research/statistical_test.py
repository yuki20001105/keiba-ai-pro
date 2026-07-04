from __future__ import annotations

import random
from typing import Any


def _mean(xs: list[float]) -> float:
    return (sum(xs) / float(len(xs))) if xs else 0.0


def _quantile(sorted_x: list[float], q: float) -> float:
    if not sorted_x:
        return 0.0
    n = len(sorted_x)
    i = int(max(0, min(n - 1, round((n - 1) * q))))
    return float(sorted_x[i])


def bootstrap_mean_diff(
    baseline: list[float],
    challenger: list[float],
    *,
    iterations: int = 2000,
    ci: float = 0.95,
    seed: int = 42,
) -> dict[str, Any]:
    if not baseline or not challenger:
        return {
            "mean_diff": 0.0,
            "ci_low": 0.0,
            "ci_high": 0.0,
            "iterations": 0,
        }

    rnd = random.Random(int(seed))
    n_b = len(baseline)
    n_c = len(challenger)

    diffs: list[float] = []
    m = max(200, min(int(iterations), 20000))
    for _ in range(m):
        sample_b = [baseline[rnd.randrange(n_b)] for _ in range(n_b)]
        sample_c = [challenger[rnd.randrange(n_c)] for _ in range(n_c)]
        diffs.append(_mean(sample_c) - _mean(sample_b))

    diffs.sort()
    alpha = max(0.001, min(0.499, (1.0 - float(ci)) / 2.0))
    return {
        "mean_diff": _mean(challenger) - _mean(baseline),
        "ci_low": _quantile(diffs, alpha),
        "ci_high": _quantile(diffs, 1.0 - alpha),
        "iterations": m,
    }


def permutation_test_mean(
    baseline: list[float],
    challenger: list[float],
    *,
    iterations: int = 5000,
    seed: int = 42,
) -> dict[str, Any]:
    if not baseline or not challenger:
        return {
            "p_value": 1.0,
            "observed_diff": 0.0,
            "iterations": 0,
        }

    rnd = random.Random(int(seed))
    observed = _mean(challenger) - _mean(baseline)

    pooled = list(baseline) + list(challenger)
    n_b = len(baseline)
    n_total = len(pooled)

    m = max(500, min(int(iterations), 50000))
    extreme = 0
    for _ in range(m):
        idx = list(range(n_total))
        rnd.shuffle(idx)
        b = [pooled[i] for i in idx[:n_b]]
        c = [pooled[i] for i in idx[n_b:]]
        d = _mean(c) - _mean(b)
        if abs(d) >= abs(observed):
            extreme += 1

    # add-one smoothing
    p_value = float(extreme + 1) / float(m + 1)
    return {
        "p_value": p_value,
        "observed_diff": observed,
        "iterations": m,
    }


def paired_win_rate(baseline: list[float], challenger: list[float]) -> dict[str, Any]:
    n = min(len(baseline), len(challenger))
    if n <= 0:
        return {"n": 0, "challenger_win_rate": 0.0}
    wins = 0
    for i in range(n):
        if float(challenger[i]) > float(baseline[i]):
            wins += 1
    return {
        "n": int(n),
        "challenger_win_rate": float(wins) / float(n),
    }
