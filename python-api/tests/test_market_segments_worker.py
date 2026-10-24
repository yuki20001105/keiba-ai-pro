"""Worker-boundary fixtures: no subprocess, fitting, or process-priority change."""
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from market_segments import modeling


@pytest.mark.parametrize("fail_on_verification", [None, 1, 2])
def test_worker_verifies_frozen_sources_before_and_after_fit(tmp_path, monkeypatch, fail_on_verification):
    script = Path(__file__).resolve().parents[2] / "scripts/run_market_edge_segments_worker.py"
    monkeypatch.setattr(sys, "path", list(sys.path))
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        monkeypatch.setenv(name, os.environ.get(name, "1"))
    spec = importlib.util.spec_from_file_location("isolated_segment_worker_fixture", script)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    monkeypatch.setattr(worker, "ROOT", tmp_path)
    directory = tmp_path / "reports/generated/model-experiments/fixture"
    output = directory / "worker"
    plan = directory / "segment_plan.json"
    monkeypatch.setattr(sys, "argv", [str(script), "--data", str(directory / "data.parquet"),
        "--feature-spec", str(directory / "feature_spec.json"), "--plan", str(plan), "--output", str(output),
        "--expected-plan-sha256", "frozen-plan", "--expected-feature-spec-sha256", "frozen-spec",
        "--group", "B", "--model", "logistic", "--fold", "development"])
    process = SimpleNamespace(nice=lambda _: None, memory_info=lambda: SimpleNamespace(rss=1024))
    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(Process=lambda: process,
        BELOW_NORMAL_PRIORITY_CLASS=0, Error=RuntimeError))
    events = []

    def verify(path, digest):
        assert path == plan and digest == "frozen-plan"
        events.append("verify")
        if events.count("verify") == fail_on_verification:
            raise ValueError("Frozen Phase-2 source changed")

    def no_real_fit(*args, **kwargs):
        events.append("fit")
        output.mkdir(parents=True)
        return {"status": "fixture-completed"}

    monkeypatch.setitem(sys.modules, "run_market_edge_segments_analysis", SimpleNamespace(verify_plan=verify))
    monkeypatch.setattr(modeling, "train_predict", no_real_fit)
    if fail_on_verification:
        with pytest.raises(ValueError, match="Frozen Phase-2 source changed"):
            worker.main()
        assert events == (["verify"] if fail_on_verification == 1 else ["verify", "fit", "verify"])
        assert not (output / "resources.json").exists()
        if fail_on_verification == 1:
            assert not output.exists()
    else:
        worker.main()
        assert events == ["verify", "fit", "verify"]
        assert (output / "resources.json").exists()
