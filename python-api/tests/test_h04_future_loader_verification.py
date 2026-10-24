"""Minimal offline test of invoking only the frozen daily loader method."""
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/verify_h04_future_history_loader.py'
spec = importlib.util.spec_from_file_location('verify_h04_future_loader_under_test', SCRIPT)
cli = importlib.util.module_from_spec(spec); sys.modules[spec.name] = cli; spec.loader.exec_module(cli)
NOW = datetime(2026, 10, 17, 9, tzinfo=timezone.utc)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


@pytest.fixture
def harness(tmp_path, monkeypatch):
    import psutil
    runtime = tmp_path / 'actual-runtime'; directory = runtime / 'daily/20261018'; directory.mkdir(parents=True)
    state = {'state': {'target_date':'20261018', 'history_max_source_date':'20261017', 'history_row_count':8, 'history_race_count':2}}
    write(directory / 'history_state.json', state)
    write(directory / 'prior_bloodlines.json', {'target_date':'20261018','horses':{}})
    write(directory / 'preparation_manifest.json', {'target_date':'20261018','status':'completed',
        'history_max_source_date':'20261017','state_sha256':cli.preparation.file_hash(directory / 'history_state.json'),
        'bloodline_sha256':cli.preparation.file_hash(directory / 'prior_bloodlines.json'), 'snapshot_sha256':'a'*64})
    output = tmp_path / 'new-verification'
    monkeypatch.setattr(cli.preparation, 'validate_output', lambda path: output)
    monkeypatch.setattr(cli.preparation, 'protection_receipt', lambda **kw: {'old_bindings':'unchanged'})
    monkeypatch.setattr(cli.preparation, 'capture_idle_receipt', lambda *args, **kw: {})
    monkeypatch.setattr(psutil, 'virtual_memory', lambda: SimpleNamespace(available=5*1024**3))
    return runtime, directory, output


def test_loads_only_into_fake_binding_without_runtime_constructor(harness, monkeypatch):
    from forward_runtime.service import Runtime
    runtime, directory, output = harness
    def forbidden_ctor(*args, **kwargs):
        raise AssertionError('actual Runtime constructor must not be invoked')
    monkeypatch.setattr(Runtime, '__init__', forbidden_ctor)
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    result = cli.verify_loader(target_date='20261018', output_dir=output, load=True, now=NOW, runtime_root=runtime)
    assert result['passed'] is True and result['loaded_history_rows'] == 8
    assert (output / 'daily-bindings/20261018.json').exists()
    assert not (runtime / 'daily-bindings/20261018.json').exists()
    assert before == {p.name: p.read_bytes() for p in directory.iterdir()}


def test_dryrun_does_not_load_or_create_output(harness):
    runtime, directory, output = harness
    result = cli.verify_loader(target_date='20261018', output_dir=output, now=NOW, runtime_root=runtime)
    assert result['operation'] == 'dry_run' and not output.exists()


def test_memory_guard_refuses_without_output(harness, monkeypatch):
    import psutil
    runtime, directory, output = harness
    monkeypatch.setattr(psutil, 'virtual_memory', lambda: SimpleNamespace(available=3*1024**3))
    with pytest.raises(ValueError, match='four-gib-memory'):
        cli.verify_loader(target_date='20261018', output_dir=output, load=True, now=NOW, runtime_root=runtime)
    assert not output.exists()

