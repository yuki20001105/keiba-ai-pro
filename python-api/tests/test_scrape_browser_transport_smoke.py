"""Real headless browser, entirely mocked transport: never calls netkeiba."""
import asyncio

import pytest

from scraping import browser_transport
from scraping.fetch_pipeline import FetchResult


def test_browser_ajax_uses_shared_transport_and_does_not_follow_redirect(monkeypatch):
    playwright = pytest.importorskip("playwright.async_api")
    calls = []
    root = "https://race.netkeiba.com/"

    async def fetch(session, url, **kwargs):
        calls.append(url)
        if url == root:
            body = b'''<html><script>
              fetch('/api/odds').then(r=>r.json()).then(x=>window.odds=x.odds);
              </script><iframe src="/redirect"></iframe></html>'''
            headers = {"Content-Type": "text/html"}
            status = 200
        elif url == root + "api/odds":
            body, status = b'{"odds":2.5}', 200
            headers = {"Content-Type": "application/json"}
        elif url == root + "redirect":
            body, status = b"", 302
            headers = {"Location": root + "unpaced-redirect-target"}
        else:
            raise AssertionError(f"Unexpected mocked browser request: {url}")
        return FetchResult(url, url, status, body, "network", 1, response_headers=headers)

    monkeypatch.setattr(browser_transport, "fetch_bytes", fetch)

    async def run():
        async with playwright.async_playwright() as engine:
            try:
                browser = await engine.chromium.launch(headless=True)
            except playwright.Error as exc:
                if "Executable doesn't exist" in str(exc):
                    pytest.skip("Local Chromium is not installed; no download attempted")
                raise
            try:
                page = await browser.new_page(service_workers="block")
                errors = await browser_transport.install_paced_routes(page, object())
                await page.goto(root, wait_until="load", timeout=10000)
                await page.wait_for_function("window.odds === 2.5", timeout=10000)
                assert await page.evaluate("window.odds") == 2.5
                assert errors == []
            finally:
                await browser.close()

    asyncio.run(run())
    assert sorted(calls) == sorted([root, root + "api/odds", root + "redirect"])
