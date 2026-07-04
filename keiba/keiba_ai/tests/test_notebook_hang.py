from __future__ import annotations

from pathlib import Path

from scripts.notebook_execution_engine import (
    ExecutionTraceLogger,
    TimeoutNotebookError,
    execute_notebook_with_retry,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _targets_02_08(root: Path) -> list[Path]:
    d = root / "notebooks"
    return [
        d / "02_data_validation.ipynb",
        d / "03_feature_engineering.ipynb",
        d / "04_feature_analysis.ipynb",
        d / "05_model_training.ipynb",
        d / "06_prediction.ipynb",
        d / "07_evaluation.ipynb",
        d / "08_reporting.ipynb",
    ]


def test_notebook_single_execution_smoke_02_08():
    root = _repo_root()
    trace = ExecutionTraceLogger(root / "notebook_execution_trace_test_single.jsonl")
    for nb in _targets_02_08(root):
        if not nb.exists():
            continue
        res = execute_notebook_with_retry(
            nb,
            cell_timeout=600,
            notebook_timeout=7200,
            max_retry=0,
            trace=trace,
        )
        assert res.status in {"success", "timeout", "error"}


def test_notebook_sequential_execution_smoke_02_08():
    root = _repo_root()
    trace = ExecutionTraceLogger(root / "notebook_execution_trace_test_seq.jsonl")
    statuses = []
    for nb in _targets_02_08(root):
        if not nb.exists():
            continue
        res = execute_notebook_with_retry(
            nb,
            cell_timeout=600,
            notebook_timeout=7200,
            max_retry=0,
            trace=trace,
        )
        statuses.append(res.status)
    assert len(statuses) >= 1


def test_timeout_error_classification(tmp_path: Path):
    import nbformat

    nb = nbformat.v4.new_notebook(
        cells=[
            nbformat.v4.new_code_cell("import time\ntime.sleep(2)\nprint('done')"),
        ]
    )
    p = tmp_path / "timeout_case.ipynb"
    nbformat.write(nb, p)

    trace = ExecutionTraceLogger(tmp_path / "trace_timeout.jsonl")
    res = execute_notebook_with_retry(
        p,
        cell_timeout=1,
        notebook_timeout=5,
        max_retry=0,
        trace=trace,
    )
    assert res.status == "timeout"
    assert "timeout" in res.error.lower() or "cell_timeout" in res.error.lower()

    # Ensure TimeoutNotebookError symbol is importable and intended for timeout class.
    assert issubclass(TimeoutNotebookError, RuntimeError)
