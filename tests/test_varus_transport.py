"""Synthetic raw HTTP streams; never used as live results."""

import gzip
import importlib.util

import httpx
import pytest

from marketplace_mcp.errors import MarketplaceError


@pytest.fixture
def transport_module():
    assert importlib.util.find_spec("marketplace_mcp.varus_transport"), "VARUS transport missing"
    from marketplace_mcp import varus_transport

    return varus_transport


@pytest.mark.parametrize(
    "url",
    [
        "http://varus.ua/x",
        "https://evil.test/x",
        "https://varus.ua.evil/x",
        "https://user@varus.ua/x",
        "https://varus.ua:444/x",
        "https://varus.ua/../x",
        "https://varus.ua/%2e%2e/x",
        "https://varus.ua/a#x",
        "https://varus.ua/a\\b",
    ],
)
def test_url_boundary(transport_module, url):
    with pytest.raises(ValueError):
        transport_module.validate_url(url)


async def test_identity_fetch_and_cache(transport_module):
    calls = []

    def handler(req):
        calls.append(req)
        assert req.headers["accept-encoding"] == "identity"
        return httpx.Response(200, stream=httpx.ByteStream(b"public"))

    t = transport_module.VarusTransport(transport=httpx.MockTransport(handler), min_gap=0)
    assert await t.fetch("https://varus.ua/x") == "public"
    assert await t.fetch("https://varus.ua/x") == "public"
    assert len(calls) == 1
    assert t.verify is True and t.trust_env is False


async def test_compression_rejected_before_reads(transport_module):
    class Bomb(httpx.AsyncByteStream):
        read = False
        closed = False

        async def __aiter__(self):
            self.read = True
            yield gzip.compress(b"x" * 1000000)

        async def aclose(self):
            self.closed = True

    bomb = Bomb()
    t = transport_module.VarusTransport(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=bomb)
        ),
        min_gap=0,
    )
    with pytest.raises(MarketplaceError) as e:
        await t.fetch("https://varus.ua/x")
    assert e.value.code == "unsupported_content_encoding"
    assert not bomb.read and bomb.closed


async def test_wire_limit_not_content_length(transport_module):
    t = transport_module.VarusTransport(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, headers={"Content-Length": "1"}, stream=httpx.ByteStream(b"x" * 100)
            )
        ),
        max_bytes=10,
        min_gap=0,
    )
    with pytest.raises(MarketplaceError) as e:
        await t.fetch("https://varus.ua/x")
    assert e.value.code == "body_too_large"


async def test_redirect_revalidates_without_contacting_outside(transport_module):
    calls = []

    def handler(r):
        calls.append(str(r.url))
        return httpx.Response(302, headers={"Location": "https://evil.test/x"}, stream=httpx.ByteStream(b""))

    t = transport_module.VarusTransport(transport=httpx.MockTransport(handler), min_gap=0)
    with pytest.raises(MarketplaceError) as e:
        await t.fetch("https://varus.ua/x")
    assert e.value.code == "unsafe_redirect"
    assert len(calls) == 1


@pytest.mark.parametrize(
    "status,body,code,count",
    [
        (429, b"rate", "rate_limited", 1),
        (503, b"down", "transient_http", 3),
        (200, b"<title>Just a moment...</title>", "captcha", 1),
        (404, b"missing", "not_found", 1),
    ],
)
async def test_failures_are_bounded(transport_module, status, body, code, count):
    calls = []

    def handler(r):
        calls.append(r)
        return httpx.Response(status, stream=httpx.ByteStream(body))

    t = transport_module.VarusTransport(transport=httpx.MockTransport(handler), min_gap=0)
    with pytest.raises(MarketplaceError) as e:
        await t.fetch("https://varus.ua/x")
    assert e.value.code == code and len(calls) == count


async def test_cached_document_retains_fetch_time_and_upstream_age(transport_module):
    t = transport_module.VarusTransport(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, headers={"Age": "955", "X-Cache-Stale": "true"}, stream=httpx.ByteStream(b"public")
            )
        ),
        min_gap=0,
    )
    assert hasattr(t, "fetch_document"), "Fetch metadata is missing"
    first = await t.fetch_document("https://varus.ua/x")
    second = await t.fetch_document("https://varus.ua/x")
    assert first["fetched_at"] == second["fetched_at"]
    assert first["upstream_age_seconds"] == 955
    assert first["upstream_cache_stale"] is True
    assert await t.fetch("https://varus.ua/x") == "public"


async def test_uncached_geocoder_channel_cancels_without_orphan_request(transport_module):
    import asyncio

    started = asyncio.Event()

    class Waiting(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            started.set()
            await asyncio.Event().wait()
            yield b"never"

        async def aclose(self):
            self.closed = True

    stream = Waiting()
    t = transport_module.VarusTransport(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=stream)), min_gap=0
    )
    assert hasattr(t, "fetch_uncached"), "Geocoder needs a cancellation-bound channel"
    task = asyncio.create_task(t.fetch_uncached("https://varus.ua/x"))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.closed and t.cache.inflight_size == 0
