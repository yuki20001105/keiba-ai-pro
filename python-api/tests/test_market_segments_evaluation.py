import numpy as np
import pandas as pd
import pytest

from market_segments.evaluation import compare_day_bootstrap, simultaneous_bands, evaluate, payout_concentration


def test_day_pairing_and_constant_difference():
    value, samples = compare_day_bootstrap(np.array([.2, .2, .2]), ["20220101", "20220101", "20220102"], samples=100)
    assert value["point"] == pytest.approx(.2)
    assert np.allclose(samples, .2)
    assert value["days"] == 2


def test_shared_draws_include_days_with_no_member():
    value, samples = compare_day_bootstrap(np.array([.1]), ["20220101"], all_days=["20220101", "20220102"], samples=100)
    assert 0 < value["usable_samples"] < 100
    assert np.allclose(samples[np.isfinite(samples)], .1)


def test_simultaneous_bands_never_drop_bad_family():
    records = [{"point": 0.}, {"point": 1.}]
    info = simultaneous_bands(records, [np.arange(10) / 100 - .045, np.arange(10) / 100 + .955])
    assert info["comparisons"] == 2
    assert records[0]["family_ci95_low"] < 0 < records[0]["family_ci95_high"]
    with pytest.raises(ValueError):
        simultaneous_bands(records, [np.array([0., np.nan]), np.array([1., 1.])])


def fixture():
    rows = []
    for date, phase in [("20211001", "development"), ("20211002", "development"), ("20220101", "validation"), ("20220701", "validation")]:
        for horse in [1, 2]:
            rows.append({"race_id": date + "0001", "race_date": date, "phase": phase,
                "horse_number": horse, "finish": horse, "odds": float(horse + 1),
                "expected_runner_count": 2, "probability": .6 if horse == 1 else .4,
                "tansho_payout": 200. if horse == 1 else np.nan, "payout_confirmed_race": True})
    data = pd.DataFrame(rows)
    flags = data[["race_id"]].drop_duplicates().assign(example=True, empty=False)
    return data, flags


def test_same_cohort_baseline_duplicate_is_inconclusive_and_empty_cells_kept():
    frame, flags = fixture()
    metrics, intervals, candidates, family = evaluate({"A_Market": frame, "B_same": frame.copy()}, flags,
        ["example", "empty"], minimum_races=1, minimum_runners=2, samples=100)
    assert len(metrics) == 12
    assert intervals.point.eq(0).all()
    assert set(candidates.candidate) == {"INCONCLUSIVE", "UNDERPOWERED"}
    assert not candidates.production_adoption.any()


def test_cohort_mismatch_rejected():
    frame, flags = fixture()
    bad = frame.iloc[:-1].copy()
    with pytest.raises(ValueError, match="same-cohort"):
        evaluate({"A_Market": frame, "B_bad": bad}, flags, ["example"], samples=20)


def test_unknown_segment_is_not_silently_negative():
    frame, flags = fixture()
    flags["unknown"] = pd.NA
    metrics, _, candidates, _ = evaluate({"A_Market": frame, "B_same": frame.copy()}, flags,
        ["unknown"], samples=20)
    cells = metrics.loc[metrics.segment.eq("unknown")]
    assert cells.unknown_segment_races.eq(2).all()
    assert cells.status.eq("no_evaluable_races").all()
    assert candidates.candidate.eq("UNDERPOWERED").all()


@pytest.mark.parametrize("options", [
    {"all_days": ["20220102"]},
    {"all_days": ["20220101", "20220101"]},
    {"all_days": []}, {"all_days": ["20220101", None]},
    {"samples": 1}, {"samples": True},
    {"samples": 2, "draws": np.array([[0.], [0.]])},
    {"samples": 2, "draws": np.array([[1], [0]])},
    {"samples": 2, "draws": np.array([[-1], [0]])},
    {"samples": 2, "draws": np.array([[0]])},
    {"samples": 2, "all_days": ["20220101", "20220102"], "draws": np.ones((2, 2), dtype=int)},
])
def test_bootstrap_rejects_malformed_or_empty_shared_samples(options):
    with pytest.raises(ValueError):
        compare_day_bootstrap([.1], ["20220101"], **options)


@pytest.mark.parametrize("records,columns", [
    ([{"point": 0.}], []), ([], [np.zeros(2)]),
    ([{"point": 0.}], [np.zeros(1)]),
    ([{"point": np.nan}], [np.zeros(2)]),
    ([{"point": 0.}, {"point": 0.}], [np.zeros(2), np.zeros(3)]),
])
def test_entire_family_must_have_finite_points_and_aligned_draws(records, columns):
    with pytest.raises(ValueError):
        simultaneous_bands(records, columns)


@pytest.mark.parametrize("field,value", [("phase", "development"), ("payout_confirmed_race", False)])
def test_same_key_but_changed_evaluation_metadata_is_rejected(field, value):
    frame, flags = fixture()
    bad = frame.copy()
    bad.loc[bad.race_date.eq("20220101"), field] = value
    with pytest.raises(ValueError, match="same-cohort-required:" + field):
        evaluate({"A_Market": frame, "B_bad": bad}, flags, ["example"], samples=20)


def test_string_false_is_not_a_true_segment():
    frame, flags = fixture()
    flags["example"] = "False"
    with pytest.raises(ValueError, match="boolean-or-unknown"):
        evaluate({"A_Market": frame}, flags, ["example"], samples=20)


def test_insufficient_development_cannot_be_a_lead_even_when_validation_is_strong():
    frame, flags = fixture()
    flags.loc[flags.race_id.eq("202110020001"), "example"] = False
    improved = frame.copy()
    improved["probability"] = np.where(improved.finish.eq(1), .8, .2)
    _, intervals, candidates, _ = evaluate({"A_Market": frame, "B_improved": improved}, flags,
        ["example"], minimum_races=2, minimum_runners=4, samples=100)
    row = candidates.iloc[0]
    assert row.validation_sample_pass and not row.development_sample_pass
    assert row.candidate == "UNDERPOWERED"
    assert not row.sample_pass and not row.independent_validation and not row.production_adoption
    assert row.selection_calibration_must_review and row.payout_concentration_must_review
    validation = intervals.loc[intervals.period.eq("validation")]
    assert validation.validation_H1_races.eq(1).all() and validation.validation_H2_days.eq(1).all()


def test_positive_probability_lead_is_explicitly_not_a_profit_or_adoption_claim():
    frame, flags = fixture()
    improved = frame.copy()
    improved["probability"] = np.where(improved.finish.eq(1), .8, .2)
    metrics, _, candidates, _ = evaluate({"A_Market": frame, "B_improved": improved}, flags,
        ["example"], minimum_races=2, minimum_runners=4, samples=100)
    row = candidates.iloc[0]
    assert row.candidate == "PROBABILITY_IMPROVEMENT_LEAD"
    assert row.candidate_scope == "exploratory probability improvement only"
    assert not row.independent_validation and not row.production_adoption
    for label in ["top1", "fixed_EV_gt_1_10"]:
        assert label + "_calibration_ratio_actual_over_predicted" in metrics
        assert label + "_roi_excluding_largest_ticket" in metrics
        assert label + "_roi_excluding_top3_tickets" in metrics


def test_jackpot_concentration_and_ticket_removal_are_descriptive_only():
    selected = pd.DataFrame({"finish": [1, 2, 2, 2, 2], "tansho_payout": [1000., np.nan, np.nan, np.nan, np.nan],
        "payout_confirmed_race": True})
    receipt = payout_concentration(selected)
    assert receipt["maximum_payout_yen"] == 1000
    assert receipt["top1_share_of_total_payout"] == 1
    assert receipt["top3_share_of_total_payout"] == 1
    assert receipt["roi_excluding_largest_ticket"] == 0
    assert receipt["roi_excluding_top3_tickets"] == 0
    selected.loc[1, "payout_confirmed_race"] = False
    assert all(value is None for value in payout_concentration(selected).values())


def test_fixed_100_yen_stake_and_empty_remaining_tickets():
    selected = pd.DataFrame({"finish": [1, 1, 1, 1], "tansho_payout": [1000., 300., 200., 100.]})
    receipt = payout_concentration(selected)
    assert receipt["roi_excluding_largest_ticket"] == 200
    assert receipt["roi_excluding_top3_tickets"] == 100
    assert receipt["top3_share_of_total_payout"] == 1500 / 1600
    assert payout_concentration(selected.head(1))["roi_excluding_largest_ticket"] is None
