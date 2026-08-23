from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
PY_API_DIR = ROOT_DIR / "python-api"
if str(PY_API_DIR) not in sys.path:
    sys.path.insert(0, str(PY_API_DIR))

from scraping.fetch_pipeline import fetch_text, get_fetch_metrics


class _FakeResponse:
    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self._body = body
        self.headers = headers or {}

    async def read(self) -> bytes:
        return self._body


class _FakeRequestCtx:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeResponse:
        return self._response

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeSession:
    def __init__(self, plan: dict[str, list[_FakeResponse]]) -> None:
        self._plan = {k: list(v) for k, v in plan.items()}
        self.calls: dict[str, int] = {}

    def get(self, url: str):
        self.calls[url] = int(self.calls.get(url, 0)) + 1
        queue = self._plan.get(url)
        if not queue:
            return _FakeRequestCtx(_FakeResponse(404, b""))
        if len(queue) == 1:
            return _FakeRequestCtx(queue[0])
        return _FakeRequestCtx(queue.pop(0))


async def _run_checks() -> dict[str, Any]:
    ts = int(time.time())
    u_cache = f"https://example.test/cache/{ts}"
    u_dup = f"https://example.test/dup/{ts}"
    u_retry = f"https://example.test/retry/{ts}"
    u_dry = f"https://example.test/dry/{ts}"

    fake = _FakeSession(
        {
            u_cache: [_FakeResponse(200, b"cache-ok")],
            u_dup: [_FakeResponse(200, b"dup-ok")],
            u_retry: [
                _FakeResponse(429, b"", headers={"Retry-After": "1"}),
                _FakeResponse(503, b""),
                _FakeResponse(200, b"retry-ok"),
            ],
        }
    )

    base_metrics = get_fetch_metrics(reset=True)

    r1, t1 = await fetch_text(fake, u_cache, cache_ttl_sec=3600, min_interval_sec=1.0)
    r2, t2 = await fetch_text(fake, u_cache, cache_ttl_sec=3600, min_interval_sec=1.0)

    d1 = fetch_text(fake, u_dup, cache_ttl_sec=3600, min_interval_sec=1.0)
    d2 = fetch_text(fake, u_dup, cache_ttl_sec=3600, min_interval_sec=1.0)
    (rd1, td1), (rd2, td2) = await asyncio.gather(d1, d2)

    slept: list[float] = []
    _orig_sleep = asyncio.sleep

    async def _fake_sleep(seconds: float) -> None:
        slept.append(float(seconds))
        return None

    asyncio.sleep = _fake_sleep  # type: ignore[assignment]
    try:
        rr, tr = await fetch_text(
            fake,
            u_retry,
            cache_ttl_sec=0,
            min_interval_sec=1.0,
            max_retries=3,
            retry_statuses={429, 503},
            retry_base_sec=0.5,
            retry_jitter_sec=0.0,
            circuit_threshold=2,
            circuit_cooldown_sec=30.0,
        )
    finally:
        asyncio.sleep = _orig_sleep  # type: ignore[assignment]

    dry, _ = await fetch_text(fake, u_dry, dry_run=True, min_interval_sec=1.0)

    metrics = get_fetch_metrics(reset=True)

    checks = {
        "cache_hit_no_fetch": bool(r1.status == 200 and r2.source == "cache" and fake.calls.get(u_cache, 0) == 1 and t1 == t2),
        "duplicate_url_collapsed": bool(rd1.status == 200 and rd2.status == 200 and fake.calls.get(u_dup, 0) == 1 and td1 == td2),
        "retry_after_backoff_safe": bool(rr.status == 200 and rr.attempts == 3 and len([s for s in slept if s >= 1.0]) >= 1),
        "dry_run_no_access": bool(dry.source == "dry-run" and fake.calls.get(u_dry, 0) == 0),
    }

    return {
        "base_metrics": base_metrics,
        "metrics": metrics,
        "calls": fake.calls,
        "retry_sleeps": slept,
        "results": {
            "cache": {"status": r1.status, "source_second": r2.source},
            "dup": {"status1": rd1.status, "status2": rd2.status},
            "retry": {"status": rr.status, "attempts": rr.attempts, "source": rr.source},
            "dry": {"status": dry.status, "source": dry.source},
        },
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test for cached rate-limited fetch pipeline (stub/mock only)")
    parser.parse_args()

    payload = asyncio.run(_run_checks())
    checks = payload.get("checks", {}) if isinstance(payload, dict) else {}
    success = bool(checks) and all(bool(v) for v in checks.values())

    result = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "success": success,
        "verdict": "pass" if success else "fail",
        "verdict_reason": "ok" if success else "fetch-pipeline-smoke-failed",
        **payload,
    }

    reports = Path("reports")
    reports.mkdir(parents=True, exist_ok=True)
    out = reports / "fetch_pipeline_smoke_result.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "result_file": str(out),
        "success": success,
        "verdict": result["verdict"],
        "checks": checks,
    }, ensure_ascii=False))

    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
