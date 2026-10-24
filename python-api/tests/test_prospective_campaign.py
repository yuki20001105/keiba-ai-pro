from copy import deepcopy
from pathlib import Path

import pytest

from prospective.campaign import PLAN, VARIANTS, _verified_results, select_variant, prospective_manifest
from prospective.calibration import FAMILIES


def scores():
    return {f"dev-{name}": {"validation_years": [2021, 2022], "evaluation": {"overall": {"rank_correlation": .5}}} for name in VARIANTS}


def test_predeclared_families_match_implementation():
    assert PLAN["calibration_families"] == list(FAMILIES)


def test_exact_tie_retains_h04():
    assert select_variant(scores()) == "reference"


def test_better_development_rho_wins_without_roi():
    data = scores(); data["dev-surface_split"]["evaluation"]["overall"]["rank_correlation"] = .51
    for value in data.values(): value["evaluation"]["overall"]["roi"] = 9999
    assert select_variant(data) == "surface_split"


def test_no_confirm_selection():
    data = scores(); data["dev-history_shrink"]["validation_years"] = [2023]
    with pytest.raises(ValueError): select_variant(data)


def test_no_missing_candidates():
    data = scores(); del data["dev-reference"]
    with pytest.raises(ValueError): select_variant(data)


@pytest.mark.parametrize("metric", [None, float("nan"), float("inf"), 2])
def test_nonfinite_metric_rejected(metric):
    data = scores(); data["dev-reference"]["evaluation"]["overall"]["rank_correlation"] = metric
    with pytest.raises(ValueError): select_variant(data)


def test_near_tie_uses_predeclared_order():
    data = scores(); data["dev-history_damped"]["evaluation"]["overall"]["rank_correlation"] += .0000005
    assert select_variant(data) == "reference"


def test_new_sources_are_separately_bound():
    manifest = prospective_manifest()
    assert "python-api/prospective/campaign.py" in manifest
    assert "scripts/run_forward_validation.py" in manifest
    assert not any(name.startswith("python-api/experiments/") for name in manifest)


def test_no_automatic_buy_or_future_claim():
    assert PLAN["paper_only"] and PLAN["no_automatic_promotion"]
    assert not PLAN["confirmation_is_unseen"]
    assert PLAN["prospective"]["minimum_complete_races"] == 1000
