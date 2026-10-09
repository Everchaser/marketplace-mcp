import importlib

import pytest


class Provider:
    def __init__(self):
        self.calls = []

    def validate_card_url(self, url):
        from marketplace_mcp.urls import validate_url

        validate_url(url)

    async def search(self, query, limit, page):
        self.calls.append((query, limit, page))
        return {"items": [], "status": "ok"}

    async def get_card(self, url, include_reviews, review_limit):
        self.calls.append(url)
        return {"status": "ok", "url": url, "product": {"id": "1"}, "warnings": [], "error": None}


@pytest.mark.asyncio
@pytest.mark.parametrize("has_policy", [True, False])
async def test_selected_provider_owns_card_url_policy(has_policy):
    from marketplace_mcp.core import MarketplaceService

    class OtherProvider(Provider):
        def validate_card_url(self, url):
            if not url.startswith("https://other.test/item/"):
                raise ValueError("Other provider URL required")

    provider = OtherProvider()
    if not has_policy:
        provider.validate_card_url = None
    client = MarketplaceService({"other": provider})
    url = "https://other.test/item/123?variant=456"
    result = await client.get_card(url, marketplace="other")
    assert result["status"] == "ok"
    assert result["url"] == url and provider.calls == [url]
    if has_policy:
        rejected = await client.get_card("https://market.yandex.ru/product/123", marketplace="other")
        assert rejected["error"]["code"] == "validation_error"
        assert provider.calls == [url]
    invalid_reviews = await client.get_card(url, marketplace="other", include_reviews="yes")
    assert invalid_reviews["error"]["code"] == "validation_error"
    assert provider.calls == [url]
    unsupported = await client.get_card(url, marketplace="missing")
    assert unsupported["error"]["code"] == "unsupported_marketplace"
    assert provider.calls == [url]


def service(provider):
    return importlib.import_module("marketplace_mcp.core").MarketplaceService({"yandex_market": provider})


@pytest.mark.asyncio
async def test_unsupported_marketplace_never_calls_provider():
    provider = Provider()
    result = await service(provider).search("phone", marketplace="ozon")
    assert result["status"] == "error"
    assert result["error"] == {
        "code": "unsupported_marketplace",
        "message": "Unsupported marketplace: ozon",
        "retryable": False,
    }
    assert result["items"] == []
    assert not provider.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query,limit,page",
    [
        (" x ", 10, 1),
        ("a" * 201, 10, 1),
        ("ok", 0, 1),
        ("ok", 49, 1),
        ("ok", 10, 0),
        ("ok", 10, 31),
        ("ok", True, 1),
    ],
)
async def test_invalid_search_has_no_io(query, limit, page):
    provider = Provider()
    result = await service(provider).search(query, limit=limit, page=page)
    assert result["error"]["code"] == "validation_error"
    assert not provider.calls


@pytest.mark.asyncio
async def test_search_trims_query_and_dispatches():
    provider = Provider()
    result = await service(provider).search("  phone  ", limit=3, page=2)
    assert result["status"] == "ok"
    assert result["marketplace"] == "yandex_market"
    assert provider.calls == [("phone", 3, 2)]


@pytest.mark.asyncio
async def test_card_validation_before_io():
    provider = Provider()
    client = service(provider)
    for kwargs in [
        {"url": "https://evil.test/card/a/1"},
        {"url": "https://market.yandex.ru/card/a/1", "review_limit": 21},
    ]:
        assert (await client.get_card(**kwargs))["error"]["code"] == "validation_error"
    assert not provider.calls
    url = "https://market.yandex.ru/card/a/1"
    assert (await client.get_card(url))["url"] == url
    assert provider.calls == [url]


@pytest.mark.asyncio
async def test_batch_preserves_order_duplicates_and_individual_errors():
    provider = Provider()
    urls = ["https://market.yandex.ru/card/a/1", "https://evil.test/", "https://market.yandex.ru/card/a/1"]
    result = await service(provider).get_cards(urls)
    assert result["status"] == "partial"
    assert [r["url"] for r in result["results"]] == urls
    assert result["requested"] == 3 and result["returned"] == 2 and result["failed"] == 1
    assert result["results"][1]["error"]["code"] == "validation_error"


@pytest.mark.asyncio
@pytest.mark.parametrize("urls", [[], ["https://market.yandex.ru/product/1"] * 11])
async def test_batch_bounds_before_io(urls):
    provider = Provider()
    result = await service(provider).get_cards(urls)
    assert result["error"]["code"] == "validation_error"
    assert not provider.calls


@pytest.mark.asyncio
async def test_batch_concurrency_at_most_two():
    import asyncio

    class Slow(Provider):
        active = 0
        peak = 0

        async def get_card(self, *args):
            self.active += 1
            self.peak = max(self.peak, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return await super().get_card(*args)

    provider = Slow()
    await service(provider).get_cards([f"https://market.yandex.ru/product/{i}" for i in range(8)])
    assert provider.peak == 2


@pytest.mark.asyncio
async def test_provider_failures_are_structured_and_do_not_discard_batch_success():
    from marketplace_mcp.errors import MarketplaceError

    class Broken(Provider):
        async def search(self, *args):
            raise MarketplaceError("transport_error", "network failed", True)

        async def get_card(self, url, *args):
            if url.endswith("2"):
                raise MarketplaceError("not_found", "missing")
            return await super().get_card(url, *args)

    client = service(Broken())
    result = await client.search("test")
    assert result["error"]["code"] == "transport_error" and result["error"]["retryable"]
    result = await client.get_cards(
        ["https://market.yandex.ru/product/1", "https://market.yandex.ru/product/2"]
    )
    assert result["status"] == "partial" and result["returned"] == 1
    assert result["results"][1]["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_unexpected_provider_exception_does_not_discard_other_cards():
    class Crashed(Provider):
        async def get_card(self, url, *args):
            if url.endswith("2"):
                raise TypeError("raw secret error")
            return await super().get_card(url, *args)

    result = await service(Crashed()).get_cards(
        ["https://market.yandex.ru/product/1", "https://market.yandex.ru/product/2"]
    )
    assert result["status"] == "partial" and result["returned"] == 1
    assert result["results"][1]["error"]["code"] == "provider_error"
    assert "secret" not in str(result)
