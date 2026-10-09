import gzip
import importlib
import tracemalloc
from itertools import pairwise

import httpx
import pytest

from marketplace_mcp.errors import MarketplaceError

URL = "https://market.yandex.ru/card/test/123"


class TrackedStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.reads = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk

    async def aclose(self):
        self.closed = True


def streamed_response(status, *, text="", content=None, headers=None):
    # Explicit streams avoid HTTPX's eager decoding of Response(content=...).
    return httpx.Response(
        status, headers=headers, stream=TrackedStream([content if content is not None else text.encode()])
    )


def transport(handler, **kwargs):
    return importlib.import_module("marketplace_mcp.transport").SafeTransport(
        transport=httpx.MockTransport(handler), min_gap=0, **kwargs
    )


@pytest.mark.asyncio
async def test_transport_fetches_html_without_env_proxy():
    seen = []

    def handler(request):
        seen.append(request)
        return streamed_response(200, text="<html>test</html>")

    t = transport(handler)
    assert await t.fetch(URL) == "<html>test</html>"
    assert seen[0].headers["accept-language"].startswith("ru")
    assert t.timeout == 20 and t.max_bytes == 8 * 1024 * 1024
    assert t.trust_env is False and t.verify is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,body,code",
    [
        (404, "x", "not_found"),
        (429, "x", "rate_limited"),
        (403, "x", "http_error"),
        (200, "", "empty_response"),
        (503, "SmartCaptcha", "captcha"),
    ],
)
async def test_transport_distinct_errors_without_blind_captcha_retry(status, body, code):
    seen = []

    def handler(request):
        seen.append(request)
        return streamed_response(status, text=body)

    with pytest.raises(MarketplaceError) as caught:
        await transport(handler).fetch(URL)
    assert caught.value.code == code
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_body_cap_applies_to_decoded_stream():
    with pytest.raises(MarketplaceError) as caught:
        await transport(lambda r: streamed_response(200, content=b"x" * 11), max_bytes=10).fetch(URL)
    assert caught.value.code == "body_too_large"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [302, 502, 503, 504, "network"])
async def test_transient_retries_bounded_two(failure):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls < 3:
            if failure == "network":
                raise httpx.ConnectError("contains secret", request=request)
            return streamed_response(failure)
        return streamed_response(200, text="good")

    assert await transport(handler).fetch(URL) == "good"
    assert calls == 3


@pytest.mark.asyncio
async def test_exhausted_network_failure_is_safe_and_bounded():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("secret password", request=request)

    with pytest.raises(MarketplaceError) as caught:
        await transport(handler).fetch(URL)
    assert caught.value.code == "transport_error" and caught.value.retryable
    assert "secret" not in str(caught.value)
    assert calls == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "location",
    ["https://evil.test/card/test/123", "http://market.yandex.ru/card/test/123", "/admin", "/card/../123"],
)
async def test_redirect_is_validated_before_second_fetch(location):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return streamed_response(302, headers={"location": location})

    with pytest.raises(MarketplaceError) as caught:
        await transport(handler).fetch(URL)
    assert caught.value.code == "unsafe_redirect"
    assert seen == [URL]


@pytest.mark.asyncio
async def test_safe_redirect_followed_with_bounded_hops():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if len(seen) == 1:
            return streamed_response(302, headers={"location": "/card/other/124"})
        return streamed_response(200, text="good")

    assert await transport(handler).fetch(URL) == "good"
    assert seen == [URL, "https://market.yandex.ru/card/other/124"]


@pytest.mark.asyncio
async def test_redirect_loop_bounded():
    seen = []

    def handler(request):
        seen.append(request)
        return streamed_response(301, headers={"location": "/card/test/123"})

    with pytest.raises(MarketplaceError) as caught:
        await transport(handler).fetch(URL)
    assert caught.value.code == "redirect_limit"
    assert len(seen) == 4


@pytest.mark.asyncio
async def test_provider_pacing_is_shared_across_concurrent_requests_and_retries():
    import asyncio
    import time

    starts = []

    async def handler(request):
        starts.append(time.monotonic())
        return streamed_response(200, text="good")

    t = importlib.import_module("marketplace_mcp.transport").SafeTransport(
        transport=httpx.MockTransport(handler), min_gap=0.03
    )
    await asyncio.gather(*(t.fetch(URL + f"?x={i}") for i in range(3)))
    assert all(b - a >= 0.027 for a, b in pairwise(starts))


@pytest.mark.asyncio
async def test_http_cache_reuses_url_but_not_captcha_failures():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return streamed_response(200, text="SmartCaptcha" if calls == 1 else "good")

    t = transport(handler)
    with pytest.raises(MarketplaceError):
        await t.fetch(URL)
    assert await t.fetch(URL) == "good"
    assert await t.fetch(URL) == "good"
    assert calls == 2


@pytest.mark.asyncio
async def test_transport_concurrency_is_shared_and_at_most_two():
    import asyncio

    active = 0
    peak = 0

    async def handler(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return streamed_response(200, text="good")

    t = transport(handler)
    await asyncio.gather(*(t.fetch(URL + f"?sku={i}") for i in range(6)))
    assert peak == 2


@pytest.mark.asyncio
async def test_whole_request_has_bounded_deadline():
    import asyncio

    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.03)
        return streamed_response(200, text="good")

    t = transport(handler)
    t.timeout = 0.005
    with pytest.raises(MarketplaceError) as caught:
        await t.fetch(URL)
    assert caught.value.code == "transport_error"
    assert calls == 3


@pytest.mark.asyncio
async def test_captcha_redirect_is_not_followed_or_retried():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return streamed_response(302, headers={"location": "https://market.yandex.ru/showcaptcha?retpath=x"})

    with pytest.raises(MarketplaceError) as caught:
        await transport(handler).fetch(URL)
    assert caught.value.code == "captcha" and calls == 1


@pytest.mark.asyncio
async def test_gzip_bomb_rejected_before_stream_read_or_decoding():
    wire = gzip.compress(b"x" * (16 * 1024 * 1024))
    stream = TrackedStream([wire])
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, headers={"content-encoding": "gzip"}, stream=stream)

    t = transport(handler, max_bytes=1024)
    tracemalloc.start()
    try:
        with pytest.raises(MarketplaceError) as caught:
            await t.fetch(URL)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert caught.value.code == "unsupported_content_encoding"
    assert not caught.value.retryable
    assert len(seen) == 1 and seen[0].headers["accept-encoding"] == "identity"
    assert stream.reads == 0 and stream.closed
    assert peak < 512 * 1024


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding", ["deflate", "br", "zstd", "unknown", "", "identity, gzip"])
async def test_other_encodings_are_rejected_before_read_without_retry(encoding):
    stream = TrackedStream([b"untrusted encoded bytes"])
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503, headers={"content-encoding": encoding}, stream=stream)

    with pytest.raises(MarketplaceError) as caught:
        await transport(handler).fetch(URL)
    assert caught.value.code == "unsupported_content_encoding"
    assert not caught.value.retryable
    assert calls == 1 and stream.reads == 0 and stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{}, {"content-encoding": " Identity "}])
async def test_identity_raw_stream_accepts_exact_byte_limit(headers):
    stream = TrackedStream([b"x" * 512, b"x" * 512])
    assert (
        await transport(
            lambda request: httpx.Response(200, headers=headers, stream=stream), max_bytes=1024
        ).fetch(URL)
        == "x" * 1024
    )
    assert stream.reads == 2 and stream.closed


@pytest.mark.asyncio
async def test_wire_cap_stops_before_tail_even_with_false_content_length():
    stream = TrackedStream([b"x" * 512, b"x" * 512, b"x", b"must not be read"])
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200, headers={"content-encoding": "identity", "content-length": "1"}, stream=stream
        )

    with pytest.raises(MarketplaceError) as caught:
        await transport(handler, max_bytes=1024).fetch(URL)
    assert caught.value.code == "body_too_large"
    assert not caught.value.retryable
    assert len(seen) == 1 and stream.reads == 3 and stream.closed
