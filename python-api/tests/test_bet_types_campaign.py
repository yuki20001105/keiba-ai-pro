import importlib.util
from pathlib import Path
import pytest

SOURCE = Path(__file__).resolve().parents[2] / "scripts/run_market_edge_bet_types.py"
spec = importlib.util.spec_from_file_location("bet_types_campaign_test", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize("name", ["baseline-start.json", "baseline-result.json", "fixed_rule_results.csv", "common_cohort.parquet", "ev_results.csv"])
def test_existing_attempt_files_are_preserved(tmp_path, monkeypatch, name):
    path = tmp_path / name
    path.write_bytes(b"existing evidence")
    monkeypatch.setattr(module, "OUTPUT", tmp_path)
    monkeypatch.setattr(module, "verify", lambda _: {})
    monkeypatch.setattr(module, "protect", lambda: pytest.fail("must reject before protected work"))
    with pytest.raises(ValueError, match="overwritten"):
        module.baseline("dummy")
    assert path.read_bytes() == b"existing evidence"
    assert sorted(p.name for p in tmp_path.iterdir()) == [name]


def test_json_write_exclusive(tmp_path):
    path = tmp_path / "receipt.json"
    module.write(path, {"frozen": True})
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        module.write(path, {"frozen": False})
    assert path.read_bytes() == original
