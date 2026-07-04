from __future__ import annotations

from typing import Any


def optimize_kelly_portfolio(
    *,
    predictions: list[dict[str, Any]],
    bankroll: int,
    per_race_limit: int,
    min_ev: float = 1.1,
    kelly_fraction: float = 0.25,
    max_single_ratio: float = 0.05,
) -> dict[str, Any]:
    rows = []
    for p in predictions:
        odds = p.get("odds")
        prob = p.get("calibrated_probability")
        if prob is None:
            prob = p.get("p_ensemble")
        if prob is None:
            prob = p.get("p_norm")
        if odds is None or prob is None:
            continue
        try:
            odds_f = float(odds)
            prob_f = float(prob)
        except Exception:
            continue
        if odds_f <= 1.0 or prob_f <= 0.0:
            continue
        ev = prob_f * odds_f
        if ev < min_ev:
            continue
        kelly = (prob_f * odds_f - 1.0) / (odds_f - 1.0)
        if kelly <= 0:
            continue
        w = min(kelly * kelly_fraction, max_single_ratio)
        rows.append({
            "horse_id": p.get("horse_id"),
            "horse_number": p.get("horse_number"),
            "horse_name": p.get("horse_name"),
            "odds": odds_f,
            "probability": prob_f,
            "expected_value": ev,
            "kelly_weight": w,
        })

    if not rows:
        return {
            "budget": int(per_race_limit),
            "recommendations": [],
            "total_bet": 0,
            "expected_return": 0.0,
        }

    total_weight = sum(float(r["kelly_weight"]) for r in rows)
    budget = min(int(per_race_limit), int(bankroll))
    recs = []
    total_bet = 0
    expected_return = 0.0
    for r in sorted(rows, key=lambda x: float(x["expected_value"]), reverse=True):
        alloc_ratio = (float(r["kelly_weight"]) / total_weight) if total_weight > 0 else 0.0
        raw_amount = int(budget * alloc_ratio)
        # 100円単位
        amount = max(0, (raw_amount // 100) * 100)
        if amount <= 0:
            continue
        total_bet += amount
        expected_return += float(amount) * float(r["expected_value"])
        recs.append({
            **r,
            "bet_amount": amount,
        })

    return {
        "budget": budget,
        "recommendations": recs,
        "total_bet": int(total_bet),
        "expected_return": float(expected_return),
        "expected_roi": ((float(expected_return) / float(total_bet)) - 1.0) if total_bet > 0 else 0.0,
    }
