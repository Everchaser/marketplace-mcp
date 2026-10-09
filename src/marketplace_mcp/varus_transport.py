"""Isolated HTTPS host-route transport. Never uses the Yandex NIC relay."""

import asyncio
import re
import time
from datetime import UTC, datetime
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from .cache import TTLCache
from .errors import MarketplaceError


def validate_url(url, host="varus.ua"):
    try:
        if not isinstance(url, str) or len(url) > 16000 or any(ord(c) < 33 for c in url) or "\\" in url:
            raise ValueError
        p = urlsplit(url)
        path = unquote(p.path)
        if (
            p.scheme != "https"
            or p.netloc != host
            or p.fragment
            or p.username
            or any(x in {".", ".."} for x in path.split("/"))
            or "\\" in path
        ):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("URL is outside provider HTTPS policy") from None
    return url


class VarusTransport:
    def __init__(
        self,
        *,
        transport=None,
        min_gap=1.0,
        max_bytes=8 * 1024 * 1024,
        cache_ttl=60,
        host="varus.ua",
        user_agent="shopping-mcp/0.1 (read-only public catalogue)",
    ):
        self.transport = transport
        self.host = host
        self.min_gap = max(1.0, min_gap) if host == "nominatim.openstreetmap.org" else min_gap
        self.max_bytes = max_bytes
        self.verify = True
        self.trust_env = False
        self.timeout = 25
        self.cache = TTLCache(ttl=cache_ttl)
        self._lock = asyncio.Lock()
        self._gate = asyncio.Semaphore(2)
        self._last = None
        self.headers = {
            "User-Agent": user_agent,
            "Accept-Encoding": "identity",
            "Accept": "text/html,application/json",
            "Accept-Language": "uk",
        }

    async def _pace(self):
        async with self._lock:
            if self._last is not None:
                await asyncio.sleep(max(0, self.min_gap - (time.monotonic() - self._last)))
            self._last = time.monotonic()

    async def fetch(self, url):
        return (await self.fetch_document(url))["text"]

    async def fetch_uncached(self, url):
        """Direct cancellation-bound channel for the persistent geocoder cache."""
        validate_url(url, self.host)
        return (await self._retry(url))["text"]

    async def fetch_document(self, url):
        validate_url(url, self.host)
        return await self.cache.get_or_fetch(url, lambda: self._retry(url))

    async def _retry(self, url):
        for attempt in range(3):
            try:
                async with self._gate, asyncio.timeout(self.timeout):
                    return await self._once(url)
            except (httpx.TransportError, TimeoutError):
                if attempt == 2:
                    raise MarketplaceError(
                        "transport_error", "Provider network request failed", True
                    ) from None
            except MarketplaceError as exc:
                if exc.code != "transient_http" or attempt == 2:
                    raise

    async def _once(self, url):
        async with httpx.AsyncClient(
            transport=self.transport,
            trust_env=False,
            verify=True,
            follow_redirects=False,
            timeout=self.timeout,
            headers=self.headers,
        ) as client:
            for hop in range(4):
                await self._pace()
                async with client.stream("GET", url) as r:
                    if r.headers.get("content-encoding", "identity").strip().lower() != "identity":
                        raise MarketplaceError(
                            "unsupported_content_encoding", "Compressed response refused before reading"
                        )
                    body = bytearray()
                    async for chunk in r.aiter_raw(chunk_size=min(65536, self.max_bytes + 1)):
                        if len(body) + len(chunk) > self.max_bytes:
                            raise MarketplaceError("body_too_large", "Response exceeds body limit")
                        body.extend(chunk)
                    text = body.decode("utf-8", errors="replace")
                    if re.search(
                        r"<title>\s*(?:Just a moment|Access denied)|cf-chl-|g-recaptcha|hcaptcha|/showcaptcha",
                        text,
                        re.IGNORECASE,
                    ):
                        raise MarketplaceError("captcha", "Provider challenge; no bypass or retries")
                    if r.status_code in {301, 302, 303, 307, 308} and r.headers.get("location"):
                        try:
                            target = r.headers["location"]
                            if any(x in {".", ".."} for x in unquote(urlsplit(target).path).split("/")):
                                raise ValueError
                            target = urljoin(url, target)
                            validate_url(target, self.host)
                        except ValueError:
                            raise MarketplaceError(
                                "unsafe_redirect", "Redirect outside provider policy"
                            ) from None
                        if hop == 3:
                            raise MarketplaceError("redirect_limit", "Redirect hop limit reached")
                        url = target
                        continue
                    if r.status_code == 404:
                        raise MarketplaceError("not_found", "Provider page not found")
                    if r.status_code == 429:
                        raise MarketplaceError("rate_limited", "Provider rate limit; wait before retry", True)
                    if r.status_code in {502, 503, 504}:
                        raise MarketplaceError("transient_http", "Temporary upstream error", True)
                    if r.status_code != 200:
                        raise MarketplaceError("http_error", "Provider HTTP request failed")
                    if not text.strip():
                        raise MarketplaceError("empty_response", "Provider returned an empty response")
                    age = r.headers.get("age", "")
                    return {
                        "text": text,
                        "fetched_at": datetime.now(UTC).isoformat(),
                        "upstream_age_seconds": int(age)
                        if age.isascii() and age.isdigit() and len(age) < 10
                        else None,
                        "upstream_cache_stale": r.headers.get("x-cache-stale", "").lower() == "true",
                        "cache_ttl_seconds": self.cache.ttl,
                    }
