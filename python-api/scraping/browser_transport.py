"""Make the optional odds browser use the same request budget as acquisition."""
from __future__ import annotations

from urllib.parse import urlsplit

from scraping.fetch_pipeline import FetchAccessBlocked, fetch_bytes


def _allowed_url(url: str) -> bool:
    target = urlsplit(url)
    host = (target.hostname or "").lower()
    return (
        target.scheme in {"http", "https"} and target.username is None
        and (host == "netkeiba.com" or host.endswith(".netkeiba.com"))
    )


async def install_paced_routes(page, session) -> list[Exception]:
    """Return errors to check after navigation; routing errors must not vanish."""
    errors: list[Exception] = []

    async def route_request(route):
        request = route.request
        # Odds extraction needs markup/scripts/API data, not media or tracking.
        if not _allowed_url(request.url) or request.method != "GET" or request.resource_type in {
            "image", "media", "font", "ping", "other",
        }:
            await route.abort()
            return
        if errors:
            await route.abort()
            return
        try:
            response = await fetch_bytes(
                session, request.url, request_headers=await request.all_headers(),
                use_cache=False, force_refresh=True, max_retries=1,
                allow_redirects=False, max_body_bytes=8 * 1024 * 1024,
            )
            if response.source == "access-blocked":
                raise FetchAccessBlocked(response.error or "netkeiba access restricted")
            if response.status < 100:
                await route.abort()
                return
            if response.status in {301, 302, 303, 307, 308}:
                # Playwright routes only the first URL in a redirect chain.
                # Finished odds pages redirect to results, which this odds-only
                # fallback does not need. Never let the browser follow it.
                await route.abort()
                return
            headers = {
                key: value for key, value in response.response_headers.items()
                if key.lower() not in {"content-encoding", "content-length", "transfer-encoding"}
            }
            await route.fulfill(status=response.status, headers=headers, body=response.body)
        except FetchAccessBlocked as exc:
            errors.append(exc)
            await route.abort()
        except Exception:
            await route.abort()

    # Context routing covers a popup's first request too; page routing does not.
    await page.context.route("**/*", route_request)
    return errors
