"""Candidate dispatch stays explicit, local and outside active legacy models."""
import asyncio
from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException

from routers import predict, models_mgmt
from services import local_candidate_input, prediction_compute
import app_config


@pytest.fixture
def two_trusted_candidate_files(tmp_path, monkeypatch):
    """Two allowed fixture digests, never change or deserialize real artifacts."""
    specs = []
    for model_id, body in [('local_safe_m01_fixture', b'AAAA'), ('local_safe_m02_fixture', b'BBBB')]:
        path = tmp_path / f'{model_id}.joblib'
        path.write_bytes(body)
        specs.append({'model_id': model_id, 'source_path': str(path),
                      'sha256': hashlib.sha256(body).hexdigest()})
    monkeypatch.setattr(local_candidate_input, '_SPECS', {spec['model_id']: spec for spec in specs})
    monkeypatch.setattr(local_candidate_input, '_modules_verified', lambda: True)
    monkeypatch.setattr(app_config, 'APP_ENV', 'test')
    return specs


def test_explicit_candidate_loader_binds_id_before_deserializing(two_trusted_candidate_files, monkeypatch):
    first, second = two_trusted_candidate_files
    path = Path(first['source_path'])
    path.write_bytes(Path(second['source_path']).read_bytes())
    # Both digests are trusted, but the requested path still belongs to M01.
    assert local_candidate_input.validate_candidate_sources(path)['model_id'] == second['model_id']
    def forbidden(*_args, **_kwargs):
        raise AssertionError('wrong candidate must fail before deserialization')
    monkeypatch.setattr(local_candidate_input.joblib, 'load', forbidden)
    with pytest.raises(ValueError, match='untrusted-artifact-sha256'):
        predict._load_served_model_bundle(path)


def test_analyze_response_cache_rejects_other_trusted_candidate(two_trusted_candidate_files, monkeypatch):
    from models import AnalyzeRaceRequest
    first, second = two_trusted_candidate_files
    path = Path(first['source_path'])
    stat = path.stat()
    response = {'success': True, 'model_id': first['model_id'], 'race_info': {}, 'pro_evaluation': {},
                'predictions': [{'odds': 2.0}], 'bet_types': {}, 'best_bet_type': 'none',
                'best_bet_info': {}, 'race_level': '', 'recommendation': {'decision': 'skip'}}
    reads = []
    class ResponseCache(dict):
        def get(self, key):
            reads.append(key)
            return predict._time.time(), response
    monkeypatch.setattr(predict, '_ANALYZE_CACHE', ResponseCache())
    def forbidden(*_args, **_kwargs):
        raise AssertionError('response cache must not deserialize any model')
    monkeypatch.setattr(local_candidate_input.joblib, 'load', forbidden)
    request = AnalyzeRaceRequest(race_id='202605040301', model_id=first['model_id'])
    assert asyncio.run(predict._analyze_race_impl(request)).model_id == first['model_id']
    assert len(reads) == 1
    path.write_bytes(Path(second['source_path']).read_bytes())
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert (path.stat().st_mtime_ns, path.stat().st_size) == (stat.st_mtime_ns, stat.st_size)
    with pytest.raises(ValueError, match='selected-model-id-mismatch'):
        asyncio.run(predict._analyze_race_impl(request))
    assert len(reads) == 1  # The stale response was never read/admitted.


def test_explicit_local_selection_does_not_change_default(monkeypatch):
    monkeypatch.setattr(app_config, 'APP_ENV', 'development')
    spec = local_candidate_input.candidate_specs()[0]
    assert predict._resolve_model_path(spec['model_id']) == Path(spec['source_path'])
    monkeypatch.setattr(predict, 'get_active_model_id', lambda: 'preserved_active')
    monkeypatch.setattr(predict, '_ensure_model_local', lambda _id: Path('preserved_active.joblib'))
    assert predict._resolve_model_path(None) == Path('preserved_active.joblib')
    monkeypatch.setattr(app_config, 'APP_ENV', 'production')
    assert predict._local_candidate_spec(model_id=spec['model_id']) is None


@pytest.mark.parametrize('operation', [models_mgmt.activate_model, models_mgmt.delete_model])
def test_candidate_cannot_be_activated_or_deleted(operation, monkeypatch):
    monkeypatch.setenv('APP_ENV', 'development')
    monkeypatch.setenv('MODEL_ACTIVATION_LOCAL_ENABLED', 'true')
    monkeypatch.setenv('MODEL_DELETION_LOCAL_ENABLED', 'true')
    changed = []
    monkeypatch.setattr(models_mgmt, 'set_active_model_id', lambda value: changed.append(value))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(operation('local_safe_m02_2025', {}))
    assert exc.value.status_code == 409
    assert changed == []


def test_candidate_worker_uses_adapter_not_legacy_history(tmp_path, monkeypatch):
    model = tmp_path / 'candidate.joblib'; model.write_bytes(b'fixture')
    frame = pd.DataFrame({'horse_number': [1, 2]})
    bundle = {'_local_candidate': {'model_id': 'candidate'}, 'model': object(), 'target': 'speed_deviation'}
    seen = {}
    def build(actual_bundle, actual_frame, **kwargs):
        assert actual_bundle is bundle
        assert actual_frame is frame
        seen['db_path'] = kwargs['db_path']
        return pd.DataFrame({'safe_x': [1., 2.]}), {
            'history_audit': {'eligible_rows': 4}, 'issue_codes': [], 'status': 'unverified',
        }
    monkeypatch.setattr(prediction_compute, '_load_bundle', lambda path: bundle)
    monkeypatch.setattr(local_candidate_input, 'build_candidate_input', build)
    monkeypatch.setattr(local_candidate_input, 'predict_candidate_scores', lambda *a, **k: (np.array([1., 2.]), np.array([.3, .7])))
    from keiba_ai import db_ultimate_loader
    def forbidden(*a, **k): raise AssertionError('legacy history was called')
    monkeypatch.setattr(db_ultimate_loader, 'load_prediction_history_frame', forbidden)
    result = prediction_compute.compute_prediction({
        'frame': frame, 'model_path': str(model), 'db_path': str(tmp_path/'isolated.db'),
        'excluded_race_ids': ['race'], 'native_threads': 2,
    })
    assert 'error' not in result
    assert result['calibrator_applied'] is False
    assert result['history_rows'] == 4
    assert result['place3'] is None
    assert seen['db_path'] == tmp_path/'isolated.db'


@pytest.fixture
def candidate_metadata_fixture(tmp_path, monkeypatch):
    """Fixture receipts only: never deserialize a research or active model."""
    specs = deepcopy(local_candidate_input.candidate_specs())
    bodies, loads = {}, []
    fold_scores = {
        'local_safe_m01_2026': (0.18407748362909232, 0.25552914763162693, 0.7627420357943595, 2180),
        'local_safe_m02_2025': (0.43032355172419723, 0.2365299404930798, 0.7016509032729245, 3009),
    }
    pins = {}
    for spec in specs:
        path = tmp_path / spec['model_id'] / 'artifacts' / f"fold_{spec['evaluation_year']}.joblib"
        spec['source_path'] = str(path)
        correlation, physical_rmse, deviation_rmse, races = fold_scores[spec['model_id']]
        fold = {'year': spec['evaluation_year'], 'sha256': spec['sha256'], 'train_rows': 1000, 'train_races': 100,
                'evaluation': {'overall': {
                    'rank_correlation': correlation, 'rmse_speed_mps': physical_rmse,
                    'rmse': deviation_rmse, 'mae_speed_mps': 0.19,
                    'top1_win_rate': 0.23, 'race_count': races,
                    'win_roi_percent': 9999, 'win_return_rate_percent': 10099,
                }}}
        # Aggregate values intentionally differ: a fold card must not use them.
        body = json.dumps({'folds': [fold], 'evaluation': {'overall': {
            'rank_correlation': -1, 'rmse_speed_mps': 99,
        }}}).encode()
        bodies[path.parent / 'result.json'] = body
        pins[spec['model_id']] = hashlib.sha256(body).hexdigest()
    original_read = Path.read_bytes
    def read_fixture(path):
        if path in bodies:
            return bodies[path]
        return original_read(path)
    def load_fixture(path, *, model_id=None):
        loads.append((Path(path), model_id))
        return {'target': 'speed_deviation'}
    monkeypatch.setattr(Path, 'read_bytes', read_fixture)
    monkeypatch.setattr(local_candidate_input, 'candidate_specs', lambda: deepcopy(specs))
    monkeypatch.setattr(local_candidate_input, 'load_candidate_bundle', load_fixture)
    monkeypatch.setattr(models_mgmt, '_CANDIDATE_EVALUATION_PINS', pins)
    monkeypatch.setattr(app_config, 'APP_ENV', 'development')
    return SimpleNamespace(specs=specs, bodies=bodies, loads=loads, fold_scores=fold_scores)


def test_candidate_cards_report_actual_period_and_physical_fold_error(candidate_metadata_fixture):
    cards = {card['model_id']: card for card in models_mgmt._local_candidate_models()}
    assert len(cards) == 2
    actual_periods = {'local_safe_m01_2026': ('20260104', '20260913'),
                      'local_safe_m02_2025': ('20250105', '20251228')}
    for model_id, period in actual_periods.items():
        card = cards[model_id]
        evaluation = card['evaluation']
        assert (evaluation['details']['evaluation_date_from'], evaluation['details']['evaluation_date_to']) == period
        assert evaluation['rmse_unit'] == 'm/s'
        correlation, physical_rmse, deviation_rmse, races = candidate_metadata_fixture.fold_scores[model_id]
        assert evaluation['primary']['rank_correlation'] == correlation
        assert evaluation['primary']['rmse'] == physical_rmse
        assert evaluation['primary']['rmse'] != deviation_rmse
        assert evaluation['details']['evaluation_race_count'] == races
        assert evaluation['time_slices'][0]['rmse'] == physical_rmse
        assert evaluation['primary']['win_roi'] is None
        assert card['is_active'] is card['can_activate'] is card['can_delete'] is False
        assert card['is_local_candidate'] is True


def test_candidate_cards_are_hidden_in_production_before_artifact_load(candidate_metadata_fixture, monkeypatch):
    monkeypatch.setattr(app_config, 'APP_ENV', 'production')
    assert models_mgmt._local_candidate_models() == []
    assert candidate_metadata_fixture.loads == []


def test_candidate_evaluation_hash_mismatch_removes_only_bad_card(candidate_metadata_fixture):
    bad = candidate_metadata_fixture.specs[0]
    evidence = Path(bad['source_path']).parent / 'result.json'
    candidate_metadata_fixture.bodies[evidence] += b' '
    cards = models_mgmt._local_candidate_models()
    assert [card['model_id'] for card in cards] == ['local_safe_m02_2025']


@pytest.mark.parametrize('mismatch', ['fold_year', 'artifact_hash'])
def test_valid_receipt_cannot_admit_wrong_fold(candidate_metadata_fixture, monkeypatch, mismatch):
    bad = candidate_metadata_fixture.specs[0]
    evidence = Path(bad['source_path']).parent / 'result.json'
    document = json.loads(candidate_metadata_fixture.bodies[evidence])
    if mismatch == 'fold_year':
        document['folds'][0]['year'] = 2024
    else:
        document['folds'][0]['sha256'] = '0' * 64
    body = json.dumps(document).encode()
    candidate_metadata_fixture.bodies[evidence] = body
    pins = dict(models_mgmt._CANDIDATE_EVALUATION_PINS)
    pins[bad['model_id']] = hashlib.sha256(body).hexdigest()
    monkeypatch.setattr(models_mgmt, '_CANDIDATE_EVALUATION_PINS', pins)
    assert [card['model_id'] for card in models_mgmt._local_candidate_models()] == ['local_safe_m02_2025']


@pytest.mark.parametrize('ultimate', [None, True, False])
def test_model_listing_preserves_legacy_active_and_filters_candidates(tmp_path, candidate_metadata_fixture, monkeypatch, ultimate):
    active = tmp_path / 'model_legacy_active.joblib'
    other = tmp_path / 'model_legacy_standard.joblib'
    active.write_bytes(b'fixture-active')
    other.write_bytes(b'fixture-standard')
    changes = []
    monkeypatch.setattr(models_mgmt, 'MODELS_DIR', tmp_path)
    monkeypatch.setattr(models_mgmt, 'SUPABASE_DATA_ENABLED', False)
    monkeypatch.setattr(models_mgmt, 'get_active_model_id', lambda: 'model_legacy_active')
    monkeypatch.setattr(models_mgmt, 'set_active_model_id', lambda value: changes.append(value))
    bundles = {
        active: {'target': 'speed_deviation', 'ultimate_mode': True, 'metrics': {}, 'feature_columns': ['legacy'],
                 'created_at': '20261010_1200'},
        other: {'target': 'speed_deviation', 'ultimate_mode': False, 'metrics': {}, 'feature_columns': ['legacy'],
                'created_at': '20261009_1200'},
    }
    # joblib.load must see only the temporary fixture paths, never real artifacts.
    monkeypatch.setattr(models_mgmt.joblib, 'load', lambda path: deepcopy(bundles[Path(path)]))
    result = asyncio.run(models_mgmt.list_models(ultimate=ultimate))
    assert result['count'] == len(result['models'])
    assert changes == []
    candidates = [card for card in result['models'] if card.get('is_local_candidate')]
    assert len(candidates) == (0 if ultimate is False else 2)
    assert all(not card['is_active'] for card in candidates)
    if ultimate is False:
        assert [card['model_id'] for card in result['models']] == ['model_legacy_standard']
        assert all(card['ultimate_mode'] is False for card in result['models'])
    else:
        assert [card['model_id'] for card in result['models'] if card['is_active']] == ['model_legacy_active']


def test_candidate_cache_hit_rechecks_artifact_bytes_even_with_unchanged_stat(tmp_path, monkeypatch):
    path = tmp_path / 'candidate.joblib'
    path.write_bytes(b'AAAA')
    stat = path.stat()
    spec = {'model_id': 'local_safe_fixture', 'sha256': hashlib.sha256(b'AAAA').hexdigest()}
    monkeypatch.setattr(local_candidate_input, '_SPECS', {spec['model_id']: spec})
    monkeypatch.setattr(local_candidate_input, '_modules_verified', lambda: True)
    monkeypatch.setattr(predict, '_local_candidate_spec', lambda **_kwargs: spec)
    monkeypatch.setattr(prediction_compute, '_BUNDLES', OrderedDict())
    loaded = []
    def fake_load(_path, *, model_id=None):
        loaded.append((_path, model_id))
        assert model_id == spec['model_id']
        return {'target': 'speed_deviation', 'optimizer': None, '_local_candidate': spec}
    monkeypatch.setattr(local_candidate_input, 'load_candidate_bundle', fake_load)
    assert prediction_compute._load_bundle(str(path))['_local_candidate'] == spec
    assert prediction_compute._load_bundle(str(path))['_local_candidate'] == spec
    assert len(loaded) == 1
    path.write_bytes(b'BBBB')
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert (path.stat().st_mtime_ns, path.stat().st_size) == (stat.st_mtime_ns, stat.st_size)
    with pytest.raises(ValueError, match='untrusted-artifact-sha256'):
        prediction_compute._load_bundle(str(path))
    assert len(loaded) == 1


def test_candidate_cache_hit_rechecks_frozen_feature_sources(tmp_path, monkeypatch):
    path = tmp_path / 'candidate.joblib'
    path.write_bytes(b'AAAA')
    spec = {'model_id': 'local_safe_fixture', 'sha256': hashlib.sha256(b'AAAA').hexdigest()}
    source_is_valid = [True]
    monkeypatch.setattr(local_candidate_input, '_SPECS', {spec['model_id']: spec})
    monkeypatch.setattr(local_candidate_input, '_modules_verified', lambda: source_is_valid[0])
    monkeypatch.setattr(predict, '_local_candidate_spec', lambda **_kwargs: spec)
    monkeypatch.setattr(prediction_compute, '_BUNDLES', OrderedDict())
    monkeypatch.setattr(local_candidate_input, 'load_candidate_bundle', lambda _path, **_kwargs: {
        'target': 'speed_deviation', 'optimizer': None, '_local_candidate': spec,
    })
    prediction_compute._load_bundle(str(path))
    source_is_valid[0] = False
    with pytest.raises(ValueError, match='frozen-feature-source-changed'):
        prediction_compute._load_bundle(str(path))
