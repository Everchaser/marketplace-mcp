"""Bounded anonymous HTTP transport."""

import asyncio
import time
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from .cache import TTLCache
from .errors import MarketplaceError
from .urls import validate_url
from .vendor.yandex_ssr import looks_like_captcha

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Accept-Encoding": "identity",
    "Referer": "https://market.yandex.ru/",
    "Upgrade-Insecure-Requests": "1",
}


class SafeTransport:
    def __init__(self, *, transport=None, proxy=None, min_gap=1.0, max_bytes=8 * 1024 * 1024, cache_ttl=60):
        self.transport = transport
        self.proxy = proxy
        self.min_gap = min_gap
        self.timeout = 20
        self.max_bytes = max_bytes
        self.trust_env = False
        self.verify = True
        self._pacing_lock = asyncio.Lock()
        self._request_gate = asyncio.Semaphore(2)
        self._last_start = None
        self.cache = TTLCache(ttl=cache_ttl)

    async def _pace(self):
        async with self._pacing_lock:
            if self._last_start is not None:
                await asyncio.sleep(max(0, self.min_gap - (time.monotonic() - self._last_start)))
            self._last_start = time.monotonic()

    async def fetch(self, url):
        validate_url(url, allow_search=True)
        return await self.cache.get_or_fetch(url, lambda: self._fetch_retries(url))

    async def _fetch_retries(self, url):
        for attempt in range(3):
            try:
                async with self._request_gate, asyncio.timeout(self.timeout):
                    return await self._fetch_once(url)
            except (httpx.TransportError, TimeoutError):
                if attempt == 2:
                    raise MarketplaceError("transport_error", "Yandex network request failed", True) from None
            except MarketplaceError as exc:
                if not (exc.code == "transient_http" and attempt < 2):
                    raise

    async def _fetch_once(self, url):
        validate_url(url, allow_search=True)
        async with httpx.AsyncClient(
            transport=self.transport,
            proxy=self.proxy,
            timeout=self.timeout,
            trust_env=False,
            verify=True,
            follow_redirects=False,
            headers=HEADERS,
        ) as client:
            for hop in range(4):
                await self._pace()
                async with client.stream("GET", url) as response:
                    # Fail closed before reading: aiter_bytes() can inflate a tiny
                    # compressed chunk into an unbounded allocation before our cap.
                    if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
                        raise MarketplaceError(
                            "unsupported_content_encoding", "Compressed responses are not supported"
                        )
                    body = bytearray()
                    # With identity encoding, the wire and decoded byte caps coincide.
                    async for chunk in response.aiter_raw(chunk_size=min(65536, self.max_bytes + 1)):
                        if len(body) + len(chunk) > self.max_bytes:
                            raise MarketplaceError("body_too_large", "Response exceeds body limit")
                        body.extend(chunk)
                    html = body.decode("utf-8", errors="replace")
                    if looks_like_captcha(html):
                        raise MarketplaceError(
                            "captcha", "Yandex challenge encountered; no automatic retries"
                        )
                    if response.status_code in {301, 302, 303, 307, 308} and response.headers.get("location"):
                        location = response.headers["location"]
                        if unquote(urlsplit(location).path).startswith("/showcaptcha"):
                            raise MarketplaceError(
                                "captcha", "Yandex challenge redirect; no automatic retries"
                            )
                        try:
                            if any(p in {".", ".."} for p in unquote(urlsplit(location).path).split("/")):
                                raise ValueError("Dot segment")
                            target = urljoin(url, location)
                            validate_url(target, allow_search=True)
                        except ValueError:
                            raise MarketplaceError(
                                "unsafe_redirect", "Redirect target is outside the allowlist"
                            ) from None
                        if hop == 3:
                            raise MarketplaceError("redirect_limit", "Redirect hop limit reached")
                        url = target
                        continue
                    if response.status_code == 404:
                        raise MarketplaceError("not_found", "Yandex page not found")
                    if response.status_code == 429:
                        raise MarketplaceError(
                            "rate_limited", "Yandex rate limit; wait before retrying", True
                        )
                    if response.status_code in {302, 502, 503, 504}:
                        raise MarketplaceError(
                            "transient_http", f"Upstream HTTP {response.status_code}", True
                        )
                    if response.status_code != 200:
                        raise MarketplaceError("http_error", f"Upstream HTTP {response.status_code}")
                    if not html.strip():
                        raise MarketplaceError("empty_response", "Yandex returned an empty response", True)
                    return html
