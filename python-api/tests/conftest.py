from __future__ import annotations

import sys
import asyncio
from pathlib import Path

import pytest


PYTHON_API_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_API_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_API_ROOT))

KEIBA_PACKAGE_ROOT = PYTHON_API_ROOT.parent / "keiba"
if str(KEIBA_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(KEIBA_PACKAGE_ROOT))


@pytest.fixture(autouse=True)
def isolate_request_pacing(tmp_path, monkeypatch):
    """Never mutate the running app's provider cooldown or wait minutes in tests."""
    from scraping import fetch_pipeline
    from scraping.request_pacing import RequestPacer

    current = [1_800_000_000.0]

    async def advance(seconds):
        current[0] += seconds
        await asyncio.sleep(0)

    pacer = RequestPacer(
        tmp_path / "request-pacing.db", clock=lambda: current[0], sleep=advance,
    )
    monkeypatch.setattr(fetch_pipeline, "_REQUEST_PACER", pacer)
    yield
    pacer.close()
    fetch_pipeline.close_fetch_cache_connections()
