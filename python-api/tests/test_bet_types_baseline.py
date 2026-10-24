import numpy as np
import pandas as pd
import pytest
from bet_types.baseline import rank_baselines, topn_summary, rule_summary, ranking_gate, payout_stats


def frame():
    rows = []
    for race, date in [("a", "20210701"), ("b", "20211001"), ("c", "20220101"), ("d", "20220701")]:
        for i in range(1, 9):
            rows.append(dict(race_id=race, race_date=date, horse_number=i, finish=i, h04_score=-float(i), odds=float(i+1), expected_runner_count=8, tansho_payout=200 if i == 1 else 0))
    return pd.DataFrame(rows)


def test_baselines_and_no_nonwin_roi():
    races, topn, tickets = rank_baselines(frame())
    assert len(races) == 8 and len(topn) == 40 and len(tickets) == 64
    summary = topn_summary(topn)
    assert summary.loc[summary.top_n.ge(3), "trifecta_horses_in_topn"].eq(1).all()
    assert summary.loc[summary.top_n.eq(1), "top2_recall"].eq(.5).all()
    assert summary.exacta_pair_in_topn.equals(summary.quinella_pair_in_topn)
    fixed = rule_summary(tickets)
    assert fixed.loc[fixed.bet_type.ne("win"), "roi_percent"].isna().all()
    assert fixed.loc[fixed.bet_type.eq("win"), "roi_percent"].eq(200).all()
    box = fixed.loc[fixed.rule.eq("quinella_top3_box")]
    assert np.allclose(box.hit_rate, 1/3)
    assert box.race_hit_rate.eq(1).all()


def test_exact_tie_break_only_horse_number():
    value = frame().sample(frac=1, random_state=3)
    value.h04_score = 0.
    _, _, tickets = rank_baselines(value)
    assert tickets.loc[tickets.rule.eq("win_top1"), "horses"].eq("1").all()


def test_stopped_gate_equal_models():
    races, _, _ = rank_baselines(frame())
    result = ranking_gate(races, minimum_races=1, minimum_runners=8, samples=20)
    assert result["gate"] == "STOP_NO_STABLE_RANK_INCREMENT"
    assert not result["calibrator_fitting_allowed"]
    assert len(result["primary_comparisons"]) == result["multiplicity"]["comparisons"] == 8


@pytest.mark.parametrize("corruption", ["tie", "missing", "future", "nan", "duplicate", "bad_payout"])
def test_fail_closed(corruption):
    value = frame()
    if corruption == "tie": value.loc[1, "finish"] = 1
    if corruption == "missing": value = value.drop(index=0)
    if corruption == "future": value.loc[0, "race_date"] = "20230101"
    if corruption == "nan": value.loc[0, "h04_score"] = np.nan
    if corruption == "duplicate": value = pd.concat([value, value.iloc[[0]]])
    if corruption == "bad_payout": value.loc[0, "tansho_payout"] = np.nan
    with pytest.raises(ValueError): rank_baselines(value)


def test_payout_concentration():
    result = payout_stats([1000, 0, 0, 0, 0])
    assert result["roi_percent"] == 200
    assert result["top1_payout_concentration"] == 1
    assert result["max_payout_removed_roi"] == result["top3_payout_removed_roi"] == 0


def test_missing_ids_rejected():
    value = frame()
    value.loc[0, "race_id"] = None
    with pytest.raises(ValueError): rank_baselines(value)


def test_duplicate_races_rejected_by_gate():
    races, _, _ = rank_baselines(frame())
    with pytest.raises(ValueError): ranking_gate(pd.concat([races, races.iloc[[0]]]), samples=20)


def test_empty_half_year_is_none_and_not_a_lead():
    import json
    value = frame()
    value.loc[value.race_id.eq("d"), "race_date"] = "20220102"
    races, _, _ = rank_baselines(value)
    result = ranking_gate(races, minimum_races=1, minimum_runners=8, samples=20)
    assert all(row["H2_delta"] is None and row["H2_races"] == 0 and not row["lead"] for row in result["primary_comparisons"])
    json.dumps(result, allow_nan=False)


def test_win_lead_alone_cannot_enable_rank_expansion():
    races, _, _ = rank_baselines(frame())
    races.loc[races.model.eq("Market"), "win_top1"] = 0
    result = ranking_gate(races, minimum_races=1, minimum_runners=8, samples=20)
    assert result["leads"] == ["win_top1"]
    assert result["nonwin_leads"] == [] and not result["calibrator_fitting_allowed"]
