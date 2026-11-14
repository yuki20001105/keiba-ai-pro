"""Research-only field-position proxies, never actual target-race pace."""
from __future__ import annotations

import numpy as np
import pandas as pd

from training import db_context_features as context
from training import db_shared_grouped as grouped


STYLE_COLUMNS = tuple(context.FEATURE_GROUPS["style_pace"])
PROXY_COLUMNS = (
    "pace_expected_position_rank", "pace_front_rival_count", "pace_front_dominance",
    "pace_lone_front", "pace_closing_pressure_interaction", "pace_pressure_coverage",
)
FEATURE_COLUMNS = (*grouped.FEATURE_COLUMNS, *STYLE_COLUMNS, *PROXY_COLUMNS)
GROUPS = {
    "style": (*grouped.FEATURE_COLUMNS, *STYLE_COLUMNS),
    "style_field": FEATURE_COLUMNS,
}
DEFINITIONS = {
    "pace_expected_position_rank": "Within-field average rank of prior early-position mean; normalized 0..1.",
    "pace_front_rival_count": "Confirmed other front types: >=3 observed passages and front rate >=0.6; not an estimate of missing rivals.",
    "pace_front_dominance": "Own prior front rate minus mean of other >=3-passage runners.",
    "pace_lone_front": "Exactly one confirmed front type, only when every runner has >=3 passages; otherwise unknown.",
    "pace_closing_pressure_interaction": "Prior corner-position gain times confirmed front rivals / (declared field size-1).",
    "pace_pressure_coverage": "Fraction with >=3 observed prior passages and finite early-position/front-rate features.",
}


def build_style_features(target: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    required = {"race_id", "race_date", "horse_number", "num_horses"}
    if (target.empty or not required.issubset(target)
            or target.duplicated(["race_id", "horse_number"]).any()):
        raise ValueError("complete-unique-current-field-required")
    sizes = target.groupby("race_id").race_id.transform("size")
    if (sizes.le(1).any() or not pd.to_numeric(target.num_horses).eq(sizes).all()
            or target.groupby("race_id").race_date.nunique(dropna=False).ne(1).any()):
        raise ValueError("complete-consistent-current-field-required")
    extra = context._extra_features(target, history)
    result = extra.loc[:, list(STYLE_COLUMNS)].copy()
    race = target.race_id
    valid = (result.ctx_style_count5.ge(3) & result.ctx_early_position_mean5.notna()
             & result.ctx_front_rate5.notna())
    known = valid.groupby(race, sort=False).transform("sum")
    front = result.ctx_front_rate5.where(valid)
    candidate = valid & front.ge(.6)
    total = candidate.groupby(race, sort=False).transform("sum")
    rivals = (total - candidate.astype(int)).where(valid & known.ge(2))
    position = result.ctx_early_position_mean5.where(valid).groupby(
        race, sort=False,
    ).rank(method="average")
    front_sum = front.groupby(race, sort=False).transform("sum", min_count=1)
    others = (front_sum - front) / (known - 1).where(known.ge(2))
    result["pace_expected_position_rank"] = (position - 1) / (known - 1).where(known.ge(2))
    result["pace_front_rival_count"] = rivals
    result["pace_front_dominance"] = front - others
    result["pace_lone_front"] = (candidate & total.eq(1)).astype(float).where(known.eq(sizes))
    result["pace_closing_pressure_interaction"] = result.ctx_late_gain_mean5 * rivals / (sizes - 1)
    result["pace_pressure_coverage"] = known / sizes
    result = result.astype(np.float32)
    result.attrs.update(extra.attrs)
    result.attrs["supervised_pace_forecast"] = False
    return result
