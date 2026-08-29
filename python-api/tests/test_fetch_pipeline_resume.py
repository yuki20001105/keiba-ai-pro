import asyncio

from scraping import fetch_pipeline


def test_successful_resume_returns_cached_body(monkeypatch) -> None:
    monkeypatch.setattr(
        fetch_pipeline,
        "_read_cache",
        lambda _url: {
            "url": "https://example.test/race",
            "status": 200,
            "headers": {},
            "body": b"<html>cached race</html>",
            "fetched_at": 1.0,
            "expires_at": 9999999999.0,
        },
    )
    monkeypatch.setattr(
        fetch_pipeline,
        "_read_resume",
        lambda _key: {"status": "success", "http_status": 200, "attempts": 1},
    )
    monkeypatch.setattr(fetch_pipeline, "_write_resume", lambda *args: None)

    result = asyncio.run(
        fetch_pipeline.fetch_bytes(
            object(),
            "https://example.test/race",
            resume_key="race:1",
        )
    )

    assert result.source == "cache"
    assert result.body == b"<html>cached race</html>"


def test_successful_resume_without_cache_fetches_body(monkeypatch) -> None:
    monkeypatch.setattr(fetch_pipeline, "_read_cache", lambda _url: None)
    monkeypatch.setattr(
        fetch_pipeline,
        "_read_resume",
        lambda _key: {"status": "success", "http_status": 200, "attempts": 1},
    )
    monkeypatch.setattr(fetch_pipeline, "_write_cache", lambda *args: None)
    monkeypatch.setattr(fetch_pipeline, "_write_resume", lambda *args: None)

    async def fake_network_fetch(_session, url, normalized_url, **_kwargs):
        return fetch_pipeline.FetchResult(
            url=url,
            normalized_url=normalized_url,
            status=200,
            body=b"<html>fresh race</html>",
            source="network",
            attempts=1,
        )

    monkeypatch.setattr(fetch_pipeline, "_network_fetch", fake_network_fetch)

    result = asyncio.run(
        fetch_pipeline.fetch_bytes(
            object(),
            "https://example.test/race",
            resume_key="race:1",
        )
    )

    assert result.source == "network"
    assert result.body == b"<html>fresh race</html>"
