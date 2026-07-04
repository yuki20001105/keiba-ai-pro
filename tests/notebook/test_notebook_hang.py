from __future__ import annotations

import json
from pathlib import Path

from scripts.notebook_execution_engine import _timeout_status_from_error, write_detailed_execution_csv


def test_write_detailed_execution_csv_creates_required_columns(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    rows = [
        {
            "event": "cell_start",
            "notebook": "02_data_validation.ipynb",
            "cell_number": 3,
            "retry_count": 0,
            "memory_gb": 0.12,
        },
        {
            "event": "cell_end",
            "notebook": "02_data_validation.ipynb",
            "cell_number": 3,
            "status": "ok",
            "elapsed_sec": 12.34,
            "retry_count": 0,
            "memory_gb": 0.13,
        },
        {
            "event": "notebook_end",
            "notebook": "03_feature_engineering.ipynb",
            "status": "timeout",
            "elapsed_sec": 601.0,
            "attempt": 2,
            "last_cell_number": 5,
            "error": "[soft-timeout] test",
        },
    ]
    trace.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    out = tmp_path / "notebook_execution_log.csv"
    write_detailed_execution_csv(trace, out)

    text = out.read_text(encoding="utf-8")
    assert "notebook,cell,status,elapsed,retry_count,memory_gb,exception" in text
    assert "02_data_validation.ipynb,3,ok,12.340,0,0.130," in text
    assert "03_feature_engineering.ipynb,5,soft-timeout,601.000,1,,[soft-timeout] test" in text


def test_timeout_status_parser() -> None:
    assert _timeout_status_from_error("[soft-timeout] x") == "soft-timeout"
    assert _timeout_status_from_error("[hard-timeout] x") == "hard-timeout"
    assert _timeout_status_from_error("timeout") == "timeout"
