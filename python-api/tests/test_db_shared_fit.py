"""Pure fixture training: chronology, fixed inputs and honest probability gates."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from training import db_shared_fit as fit


def fixture_data(days=60, races_per_day=4):
    targets, rows = [], []
    for day in range(days):
        current = (date(2021, 1, 1) + timedelta(days=day)).strftime("%Y%m%d")
        for race in range(races_per_day):
            for horse in range(1, 4):
                targets.append({
                    'race_id': f'{current}{race:04d}', 'race_date': current,
                    'horse_id': f'horse{horse}', 'horse_number': horse, 'finish': horse,
                    'time_seconds': 95.0 + horse + (day % 4) * 0.1, 'distance': 1600,
                    'surface': 'turf', 'venue': 'Tokyo', 'num_horses': 3,
                })
                row = {name: float(horse + day % 4) for name in fit.FEATURE_COLUMNS}
                for name in fit.CATEGORICAL_COLUMNS:
                    row[name] = 'known'
                row['safe_horse_number'] = float(horse)
                row['safe_weight_change'] = np.nan
                row['safe_weather'] = 'sunny' if day < int(days * 0.6) else 'unknown_later'
                rows.append(row)
    return pd.DataFrame(targets), pd.DataFrame(rows, columns=fit.FEATURE_COLUMNS)


class FixtureBooster:
    def __init__(self, scale=1.0):
        self.scale = scale

    def predict(self, matrix, *, num_threads=1):
        return -matrix['safe_horse_number'].to_numpy() * self.scale


@pytest.fixture
def pure_fit(monkeypatch):
    calls = []
    monkeypatch.setattr(fit, 'source_hashes', lambda: {'fixture-feature-formula': 'verified'})
    monkeypatch.setattr(fit, '_resource_limits', lambda *_args: (2, 2))
    def train(matrix, target, preset, *, n_threads, progress=None, progress_start=0):
        calls.append({'matrix': matrix.copy(), 'target': target.copy(), 'preset': preset['id'], 'threads': n_threads})
        return FixtureBooster(scale=1 if preset['id'] == 'lean' else 0.5)
    monkeypatch.setattr(fit, '_train_booster', train)
    return calls


def test_split_is_whole_dates_and_all_races_are_disjoint():
    target, _ = fixture_data()
    split = fit.date_partitions(target)
    dates = {name: set(target.iloc[positions].race_date) for name, positions in split.items()}
    assert {name: len(value) for name, value in dates.items()} == {'TRAIN': 36, 'CAL': 12, 'TEST': 12}
    assert max(dates['TRAIN']) < min(dates['CAL']) < max(dates['CAL']) < min(dates['TEST'])
    assert sum(len(positions) for positions in split.values()) == len(target)
    assert not (dates['TRAIN'] & dates['CAL'] or dates['CAL'] & dates['TEST'])


@pytest.mark.parametrize('size', [True, 0.0, 0.05, 0.9, float('nan'), '0.2'])
def test_invalid_test_fraction_fails(size):
    target, _ = fixture_data()
    with pytest.raises(ValueError):
        fit.date_partitions(target, test_size=size)


def test_ui_maximum_test_fraction_preserves_fixed_calibration_fraction():
    target, _ = fixture_data()
    split = fit.date_partitions(target, test_size=0.5)
    assert {name: target.iloc[positions].race_date.nunique() for name, positions in split.items()} == {'TRAIN': 18, 'CAL': 12, 'TEST': 30}


def test_inner_selection_fit_and_final_fit_never_see_cal_or_test(pure_fit):
    target, features = fixture_data()
    bundle, result = fit.fit_shared_model(target, features)
    assert len(pure_fit) == 3
    assert {row['preset'] for row in pure_fit[:2]} == {'lean', 'standard'}
    assert pure_fit[-1]['matrix'].shape == (432, 47)
    assert max(result['inner_validation']['date_to'], result['inner_training']['date_to']) <= result['partitions']['TRAIN']['date_to']
    assert result['partitions']['TRAIN']['date_to'] < result['partitions']['CAL']['date_from']
    assert result['partitions']['CAL']['date_to'] < result['partitions']['TEST']['date_from']
    assert result['test_used_for_selection_or_fit'] is False
    assert result['cal_used_for_preset_selection'] is False
    assert bundle['encoder'].categories['safe_weather'] == ['sunny']
    assert bundle['baseline']['training_sample_count'] == 432
    assert bundle['feature_columns'] == list(fit.FEATURE_COLUMNS)
    assert result['encoder_audit']['training_column_diagnostics']['safe_weight_change'] == 'all_missing_in_training'
    assert pure_fit[-1]['matrix']['safe_weight_change'].isna().all()
    encoded_later = bundle['encoder'].transform(features.iloc[-3:])
    assert encoded_later['safe_weather'].isna().all()
    assert result['data_count'] == 432 and result['race_count'] == 144
    assert result['evaluation']['primary']['win_roi'] is None
    assert result['roi_evaluated'] is False and result['active_model_changed'] is False
    assert '既知データ' in result['evaluation']['evaluation_label']


def test_test_label_changes_cannot_change_training_or_preset(pure_fit):
    target, features = fixture_data()
    first_bundle, first = fit.fit_shared_model(target, features)
    first_calls = deepcopy(pure_fit)
    pure_fit.clear()
    altered = target.copy()
    positions = fit.date_partitions(target)['TEST']
    altered.loc[positions, 'time_seconds'] += 20
    altered.loc[positions, 'finish'] = np.tile([3, 2, 1], len(positions) // 3)
    second_bundle, second = fit.fit_shared_model(altered, features)
    assert first['selected_preset'] == second['selected_preset']
    assert first_bundle['baseline'] == second_bundle['baseline']
    assert first_bundle['calibrator'] == second_bundle['calibrator']
    for left, right in zip(first_calls, pure_fit):
        pd.testing.assert_frame_equal(left['matrix'], right['matrix'])
        np.testing.assert_array_equal(left['target'], right['target'])
    assert first['evaluation']['primary']['rank_correlation'] != second['evaluation']['primary']['rank_correlation']


def test_small_test_cannot_claim_verified_probabilities(pure_fit):
    target, features = fixture_data()
    _, result = fit.fit_shared_model(target, features)
    validation = result['probability_validation']
    assert validation['schema'] == fit.PROBABILITY_VALIDATION_SCHEMA
    assert validation['status'] == 'unverified'
    assert validation['validated'] is validation['eligible_for_ev'] is False
    assert 'validation_races_sufficient' in validation['reason_codes']
    assert 'calibration_races_sufficient' in validation['reason_codes']
    assert validation['calibration_period_start'] == '2021-02-06'
    assert validation['validation_period_end'] == '2021-03-01'
    assert validation['validation_date_count'] == 12


def test_prediction_helpers_do_not_double_encode_or_refit(pure_fit):
    target, features = fixture_data()
    bundle, _ = fit.fit_shared_model(target, features)
    current = features.iloc[-6:]
    ids = target.iloc[-6:].race_id
    raw, probabilities = fit.predict_shared_scores(bundle, current, ids)
    encoded = bundle['encoder'].transform(current)
    repeated_raw, repeated_probabilities = fit.predict_encoded_shared_scores(bundle, encoded, ids)
    np.testing.assert_array_equal(raw, repeated_raw)
    np.testing.assert_array_equal(probabilities, repeated_probabilities)
    assert len(pure_fit) == 3
    assert np.allclose(pd.Series(probabilities).groupby(ids.reset_index(drop=True)).sum(), 1)
    assert not bundle['probability_validation']['validated']


def test_source_contract_drift_blocks_serving(pure_fit, monkeypatch):
    target, features = fixture_data()
    bundle, _ = fit.fit_shared_model(target, features)
    monkeypatch.setattr(fit, 'source_hashes', lambda: {'fixture-feature-formula': 'changed'})
    with pytest.raises(ValueError, match='source-contract-mismatch'):
        fit.predict_shared_scores(bundle, features.iloc[-3:])


def test_dict_claim_cannot_replace_fitted_calibrator(pure_fit):
    target, features = fixture_data()
    bundle, _ = fit.fit_shared_model(target, features)
    bundle['calibrator'] = {'temperature': 1, 'validated': True}
    with pytest.raises(ValueError, match='calibrator-type-required'):
        fit.predict_shared_scores(bundle, features.iloc[-3:])


@pytest.mark.parametrize('mutation', ['misaligned_index', 'extra_feature', 'duplicate_runner', 'missing_target', 'partial_race'])
def test_untrustworthy_aligned_cohort_fails(mutation):
    target, features = fixture_data()
    if mutation == 'misaligned_index':
        features.index = features.index + 1
    elif mutation == 'extra_feature':
        features['finish'] = target.finish
    elif mutation == 'duplicate_runner':
        target.loc[1, 'horse_number'] = 1
    elif mutation == 'missing_target':
        target.loc[1, 'time_seconds'] = np.nan
    else:
        target.loc[0, 'num_horses'] = 2
    with pytest.raises(ValueError):
        fit._prepare(target, features)


def calibrated_cohort():
    # Exact 40%/35%/25% winner frequency: predeclared probabilities are not
    # adjusted after reading results; this only tests the gate's arithmetic.
    target, _ = fixture_data(days=40, races_per_day=10)
    for race_number, positions in enumerate(target.groupby('race_id', sort=True).indices.values()):
        remainder = race_number % 20
        winner = 0 if remainder < 8 else 1 if remainder < 15 else 2
        finishes = np.array([2, 2, 2])
        finishes[winner] = 1
        target.loc[positions, 'finish'] = finishes
    probabilities = np.tile([.4, .35, .25], len(target) // 3)
    return target, probabilities


def test_probability_metrics_and_preregistered_gate_can_pass_without_roi():
    target, probabilities = calibrated_cohort()
    metrics = fit.probability_metrics(target, probabilities)
    assert metrics['race_count'] == 400
    assert metrics['selection_ece'] == pytest.approx(0)
    assert metrics['selection_bias'] == pytest.approx(0)
    assert metrics['nll'] < metrics['uniform_nll']
    assert metrics['brier'] < metrics['uniform_brier']
    audit = {'success': True, 'boundary_reached': False, 'eligible_races': 100,
             'eligible_dates': 10, 'reason_codes': []}
    cal = pd.DataFrame({'race_date': ['20200101', '20200201']})
    result = fit.validate_probabilities(audit, metrics, cal, target)
    assert result['validated'] is result['eligible_for_ev'] is True
    assert result['status'] == 'validated'
    assert result['market_edge_verified'] is result['profitability_verified'] is False
    assert result['reason_codes'] == []


@pytest.mark.parametrize('failed', ['nll', 'brier', 'selection_ece', 'selection_absolute_bias'])
def test_each_probability_quality_gate_fails_closed(failed):
    target, probabilities = calibrated_cohort()
    metrics = fit.probability_metrics(target, probabilities)
    metrics[failed] = 99
    audit = {'success': True, 'boundary_reached': False, 'eligible_races': 100,
             'eligible_dates': 10, 'reason_codes': []}
    result = fit.validate_probabilities(audit, metrics, target, target)
    assert result['validated'] is result['eligible_for_ev'] is False
    assert result['reason_codes']


def test_dead_heat_excluded_whole_race_only_from_probability_metrics():
    target, probabilities = calibrated_cohort()
    first = target.race_id.iloc[0]
    target.loc[target.race_id.eq(first), 'finish'] = [1, 1, 3]
    metrics = fit.probability_metrics(target, probabilities)
    assert metrics['total_races'] == 400
    assert metrics['race_count'] == 399 and metrics['runner_count'] == 1197
    assert metrics['excluded_dead_heat_races'] == 1


def test_race_softmax_normalizes_independently_with_extreme_scores():
    probability = fit.race_softmax(np.array([1000., 999., -999., -1000.]), ['a', 'a', 'b', 'b'], 0.5)
    assert probability[:2].sum() == pytest.approx(1)
    assert probability[2:].sum() == pytest.approx(1)
    assert probability[0] == pytest.approx(probability[2])


@pytest.mark.parametrize('raw,ids,temperature', [([1.], ['a'], 1), ([1., np.nan], ['a', 'a'], 1),
                                               ([1., 2.], ['a', None], 1), ([1., 2.], ['a', 'a'], 0)])
def test_invalid_race_softmax_input_fails(raw, ids, temperature):
    with pytest.raises(ValueError):
        fit.race_softmax(np.asarray(raw), ids, temperature)


def test_real_temperature_fit_uses_race_multinomial_loss():
    target, probabilities = calibrated_cohort()
    scores = np.log(probabilities)
    calibrator, audit = fit.fit_temperature(target, scores)
    assert audit['success'] is True
    assert audit['boundary_reached'] is False
    assert calibrator.temperature == pytest.approx(1, abs=1e-4)
    replay = calibrator.predict_proba(scores, target.race_id)
    np.testing.assert_allclose(replay, probabilities, atol=1e-5)


def test_resource_limits_bound_cpu_and_downgrade_on_low_memory(monkeypatch):
    psutil = SimpleNamespace(
        cpu_count=lambda **_kwargs: 8,
        virtual_memory=lambda: SimpleNamespace(available=16 * 1024**3),
    )
    monkeypatch.setitem(sys.modules, 'psutil', psutil)
    assert fit._resource_limits(4, pd.DataFrame({'x': [1., 2.]})) == (2, 4)
    monkeypatch.setattr(psutil, 'virtual_memory', lambda: SimpleNamespace(available=2 * 1024**3))
    assert fit._resource_limits(4, pd.DataFrame({'x': [1., 2.]})) == (1, 4)
    monkeypatch.setattr(psutil, 'cpu_count', lambda **_kwargs: 1)
    assert fit._resource_limits(4, pd.DataFrame({'x': [1., 2.]})) == (1, 1)


@pytest.mark.parametrize('cpu_count,requested,expected', [(16, 8, 4), (1, 4, 1), (None, 4, 1)])
def test_resource_limits_without_psutil_never_assumes_spare_memory(monkeypatch, cpu_count, requested, expected):
    monkeypatch.setitem(sys.modules, 'psutil', None)
    monkeypatch.setattr(fit.os, 'cpu_count', lambda: cpu_count)
    assert fit._resource_limits(requested, pd.DataFrame({'x': [1., 2.]})) == (1, expected)


def test_real_lightgbm_small_fixture_fit_keeps_fixed47(monkeypatch):
    # A bounded real learner probe is fixture-only, with no DB/API/file outputs.
    monkeypatch.setattr(fit, 'source_hashes', lambda: {'fixture-feature-formula': 'verified'})
    monkeypatch.setattr(fit, '_resource_limits', lambda *_args: (1, 1))
    target, features = fixture_data(days=30, races_per_day=2)
    bundle, result = fit.fit_shared_model(target, features, n_threads=1)
    assert bundle['model'].num_feature() == 47
    assert bundle['model'].feature_name() == list(fit.FEATURE_COLUMNS)
    raw, probability = fit.predict_shared_scores(bundle, features.iloc[-3:], n_threads=1)
    assert np.isfinite(raw).all()
    assert probability is not None and np.isfinite(probability).all()
    assert result['native_threads'] == 1
    assert result['probability_validation']['validated'] is False
