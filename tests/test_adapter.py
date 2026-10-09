import importlib
import json

import pytest

URL = "https://market.yandex.ru/card/test-kettle/123"


def patch(collections):
    return '<noframes data-apiary="patch">' + json.dumps({"collections": collections}) + "</noframes>"


def card_html():
    # Intentionally synthetic parser input, never a live output fixture.
    return patch(
        {
            "titleV2": {"a": {"title": "Test kettle", "vendor": {"name": "Brand"}}},
            "allPrices": {"a": {"baobab": {"productId": 123, "skuId": 456}}},
            "price": {
                "a": {
                    "mainPrice": {"price": {"value": 90}},
                    "oldPrices": [{"type": "regular", "price": {"value": 100}}],
                }
            },
            "reviews": {
                "reviews": [{"rating": 5, "comment": "test"}] * 3,
                "reviewStats": {"reviewsCount": 18},
                "ratingStats": {"ratingCount": 30},
            },
            "shopInfo": {"a": {"shopName": "Shop"}},
        }
    )


class Fetcher:
    def __init__(self, html):
        self.html = html
        self.calls = []

    async def fetch(self, url):
        self.calls.append(url)
        return self.html


def provider(html):
    return importlib.import_module("marketplace_mcp.yandex").YandexMarketProvider(Fetcher(html))


@pytest.mark.asyncio
async def test_yandex_card_rejects_unsafe_url_before_transport():
    from marketplace_mcp.core import MarketplaceService

    p = provider(card_html())
    bad = "https://evil.test/card/test/123"
    with pytest.raises(ValueError):
        await p.get_card(bad)
    assert p.transport.calls == []
    client = MarketplaceService({"yandex_market": p})
    result = await client.get_cards([URL, bad])
    assert result["status"] == "partial"
    assert result["returned"] == 1 and result["failed"] == 1
    assert result["results"][1]["error"]["code"] == "validation_error"
    assert p.transport.calls == [URL]


@pytest.mark.asyncio
async def test_card_normalizes_prices_reviews_and_exact_url():
    p = provider(card_html())
    result = await p.get_card(URL, True, 2)
    product = result["product"]
    assert result["status"] == "ok" and result["url"] == URL
    assert product["id"] == "123" and product["sku_id"] == "456"
    assert product["price_rub"] == 100 and product["price_conditional_rub"] == 90
    assert "unverified" in product["conditional_price_label"]
    assert product["review_count"] == 18 and product["rating_count"] == 30
    assert product["reviews_returned"] == 2 and len(product["reviews"]) == 2
    assert product["delivery"] is None and product["delivery_status"] == "unknown"
    assert product["description"] is None and product["currency"] == "RUB"
    assert any("variant" in w for w in result["warnings"])
    assert p.transport.calls == [URL]


@pytest.mark.asyncio
async def test_card_does_not_promote_unverified_price_to_regular():
    html = (
        '<script type="application/ld+json">'
        + json.dumps({"@type": "Product", "name": "Test", "url": URL, "offers": {"price": 90}})
        + "</script>"
    )
    result = await provider(html).get_card(URL, False, 0)
    assert result["product"]["price_rub"] is None
    assert result["product"]["price_conditional_rub"] == 90
    assert result["product"]["reviews"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "html,code",
    [
        ("SmartCaptcha", "captcha"),
        (
            patch({"pageParams": {"a": {"pageId": "market:product", "params": {"productId": "123"}}}}),
            "empty_product_shell",
        ),
        ("<html>changed</html>", "parser_drift"),
    ],
)
async def test_card_failure_modes_distinct(html, code):
    result = await provider(html).get_card(URL)
    assert result["status"] == "error"
    assert result["product"] is None
    assert result["error"]["code"] == code


@pytest.mark.asyncio
async def test_search_keeps_canonical_card_links_limits_and_unknown_pagination():
    html = (
        '<script type="application/ld+json">'
        + json.dumps(
            {
                "@type": "ItemList",
                "itemListElement": [
                    {"item": {"url": URL, "name": "Test", "offers": {"price": 90}}},
                    {"item": {"url": "https://market.yandex.ru/card/other/124", "name": "Other"}},
                ],
            }
        )
        + "</script>"
    )
    p = provider(html)
    result = await p.search("test kettle", 1, 2)
    assert result["status"] == "ok"
    assert result["items"][0]["url"] == URL
    assert result["items"][0]["price_rub"] is None
    assert result["returned"] == 1 and result["total_available"] is None
    assert result["has_next_page"] is None
    assert result["extraction"] == "ld+json"
    assert p.transport.calls == ["https://market.yandex.ru/search?text=test+kettle&page=2"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/card/test/123", "/product/123", "/product--test/123"])
async def test_ldjson_product_id_comes_from_path_without_changing_source_query(path):
    exact = "https://market.yandex.ru" + path + "?sku=456&offer=a%2Bb"
    source = (
        '<script type="application/ld+json">'
        + json.dumps(
            {"@type": "ItemList", "itemListElement": [{"item": {"name": "Test", "sku": "456", "url": exact}}]}
        )
        + "</script>"
    )
    result = await provider(source).search("test", 10, 1)
    assert result["status"] == "ok" and result["returned"] == 1
    assert [(p["id"], p["sku_id"], p["url"]) for p in result["items"]] == [("123", "456", exact)]


@pytest.mark.asyncio
@pytest.mark.parametrize("has_good_item", [False, True])
async def test_ldjson_final_url_guard_rejects_wrong_sku(has_good_item):
    items = [{"item": {"name": "Wrong", "sku": "456", "url": URL + "?sku=789&offer=a%2Bb"}}]
    if has_good_item:
        items.append({"item": {"name": "Good", "sku": "456", "url": URL + "?sku=456&offer=good"}})
    source = (
        '<script type="application/ld+json">'
        + json.dumps({"@type": "ItemList", "itemListElement": items})
        + "</script>"
    )
    result = await provider(source).search("test", 10, 1)
    assert result["status"] == ("partial" if has_good_item else "error")
    assert [p["url"] for p in result["items"]] == ([URL + "?sku=456&offer=good"] if has_good_item else [])
    if not has_good_item:
        assert result["error"]["code"] == "parser_drift"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "html,status,code",
    [
        ("Попробуйте сформулировать запрос", "ok", None),
        ("SmartCaptcha", "error", "captcha"),
        ("<html>changed</html>", "error", "parser_drift"),
    ],
)
async def test_search_empty_not_confused_with_captcha_or_drift(html, status, code):
    result = await provider(html).search("test", 10, 1)
    assert result["status"] == status
    assert result["error"] is None if code is None else result["error"]["code"] == code


@pytest.mark.asyncio
async def test_zone_search_uses_source_card_link_when_parser_falls_back_to_product():
    import html as escape_html

    payload = {"type": "offer", "oskuId": "123", "marketSku": "456", "title": "Test", "price": 100}
    source = (
        '<div data-zone-name="productSnippet" data-zone-data="'
        + escape_html.escape(json.dumps(payload), quote=True)
        + '"><a href="/card/test-kettle/123?sku=456">Test</a></div>'
    )
    result = await provider(source).search("test", 10, 1)
    assert result["items"][0]["url"] == URL + "?sku=456"
    assert result["extraction"] == "zone"


def zone_html(payload, links=""):
    import html

    # Synthetic regression input, not a capture or live marketplace output.
    return (
        '<div data-zone-name="productSnippet" data-zone-data="'
        + html.escape(json.dumps(payload), quote=True)
        + '">'
        + links
        + "</div>"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("ignored_kind", ["single_quotes", "single_data_quote", "uppercase_attributes"])
async def test_zone_source_identity_ignores_snippets_not_parsed_by_vendor(ignored_kind):
    ignored = zone_html(
        {"type": "offer", "oskuId": "111", "marketSku": "222", "title": "First", "price": 100},
        '<a href="/card/First/111?sku=222">First</a>',
    )
    if ignored_kind == "single_quotes":
        ignored = ignored.replace('"', "'")
    elif ignored_kind == "single_data_quote":
        ignored = ignored.replace('data-zone-data="', "data-zone-data='").replace('"><a', "'><a")
    else:
        ignored = ignored.replace("data-zone-", "DATA-ZONE-")
    source = ignored + zone_html(
        {"type": "offer", "oskuId": "333", "marketSku": "444", "title": "Second", "price": 100},
        '<a href="/card/Second/333?sku=444">Second</a>',
    )
    result = await provider(source).search("test", 10, 1)
    assert result["status"] == "ok" and result["returned"] == 1
    assert [(p["id"], p["sku_id"], p["url"]) for p in result["items"]] == [
        ("333", "444", "https://market.yandex.ru/card/Second/333?sku=444")
    ]


@pytest.mark.asyncio
async def test_zone_identity_join_preserves_deduped_distinct_offers_after_ignored_snippet():
    payload = {"type": "offer", "oskuId": "123", "marketSku": "456", "title": "Test", "price": 100}
    source = zone_html(
        {**payload, "wareId": "ignored"}, '<a href="/card/ignored/123?sku=456&offer=ignored">Ignored</a>'
    ).replace('"', "'")
    expected = []
    for offer in ("a", "b"):
        exact = URL + f"?sku=456&offer={offer}%2Bkeep"
        snippet = zone_html({**payload, "wareId": offer}, f'<a href="{exact}">Test</a>')
        source += snippet * 2
        expected.append(exact)
    result = await provider(source).search("test", 10, 1)
    assert result["status"] == "ok" and result["returned"] == 2
    assert [p["url"] for p in result["items"]] == expected
    assert [(p["id"], p["sku_id"]) for p in result["items"]] == [("123", "456")] * 2


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["product_id", "sku_id", "row_count"])
async def test_zone_identity_join_fails_closed_on_vendor_row_drift(monkeypatch, drift):
    from marketplace_mcp.yandex import ssr

    source = "".join(
        zone_html(
            {"type": "offer", "oskuId": identifier, "marketSku": "456", "title": "Test", "price": 100},
            f'<a href="/card/test/{identifier}?sku=456">Test</a>',
        )
        for identifier in ("123", "124")
    )
    parse_search = ssr.parse_search

    def drifted_search(html):
        raw = parse_search(html)
        if drift == "row_count":
            raw["items"].pop()
        else:
            raw["items"][0][drift] = "999"
        return raw

    monkeypatch.setattr(ssr, "parse_search", drifted_search)
    result = await provider(source).search("test", 10, 1)
    assert result["status"] == ("error" if drift == "row_count" else "partial")
    assert [p["url"] for p in result["items"]] == (
        [] if drift == "row_count" else ["https://market.yandex.ru/card/test/124?sku=456"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong", ["/card/other/999?sku=456", "/card/test/123?sku=789"])
async def test_final_url_guard_rechecks_resolved_zone_candidates(monkeypatch, wrong):
    from marketplace_mcp.yandex import _SourceLinks

    feed = _SourceLinks.feed
    wrong_url = "https://market.yandex.ru" + wrong

    def drifted_feed(links, html):
        feed(links, html)
        links.observed.add(wrong_url)
        links.scoped[("123", "456", "")] = {wrong_url}

    monkeypatch.setattr(_SourceLinks, "feed", drifted_feed)
    source = zone_html({"type": "offer", "oskuId": "123", "marketSku": "456", "title": "Test", "price": 100})
    result = await provider(source).search("test", 10, 1)
    assert result["status"] == "error" and result["items"] == []
    assert result["error"]["code"] == "parser_drift"


@pytest.mark.asyncio
async def test_zone_variants_keep_each_snippet_source_url_and_query():
    source = "".join(
        zone_html(
            {"type": "offer", "oskuId": "123", "marketSku": sku, "title": "Test", "price": 100},
            f'<a href="/card/test-kettle/123?sku={sku}&amp;offer={sku}%2Bkeep">Test</a>',
        )
        for sku in ("456", "789")
    )
    result = await provider(source).search("test", 10, 1)
    assert result["status"] == "ok"
    assert [(p["sku_id"], p["url"]) for p in result["items"]] == [
        (sku, URL + f"?sku={sku}&offer={sku}%2Bkeep") for sku in ("456", "789")
    ]


@pytest.mark.asyncio
async def test_zone_source_links_match_snippet_identity_when_path_is_sku():
    source = "".join(
        zone_html(
            {"type": "offer", "oskuId": "123", "marketSku": sku, "title": "Test", "price": 100},
            f'<span><a href="/card/variant-{sku}/{sku}?offer=keep%2B{sku}">Test</a></span>',
        )
        for sku in ("456", "789")
    )
    result = await provider(source).search("test", 10, 1)
    assert result["status"] == "ok"
    assert [(p["sku_id"], p["url"]) for p in result["items"]] == [
        (sku, f"https://market.yandex.ru/card/variant-{sku}/{sku}?offer=keep%2B{sku}")
        for sku in ("456", "789")
    ]


def search_collections(url=None, slug=None):
    collections = {
        "product": {"123": {"titles": {"raw": "Test"}, "slug": slug}},
        "productShowPlace": {"a": {"productId": 123, "productSnippetId": "s", "urls": {"direct": url}}},
        "productSnippet": {"s": {"productPayload": {"title": {"value": "Test"}}}},
    }
    return (
        '<noframes data-apiary="patch">'
        + json.dumps({"widgets": {"w": {"p": {"options": {"collections": collections}}}}})
        + "</noframes>"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["zone", "ssr", "ssr_slug"])
async def test_search_rejects_parser_generated_urls_without_observed_source(kind):
    source = (
        zone_html({"type": "offer", "oskuId": "123", "marketSku": "456", "title": "Test", "price": 100})
        if kind == "zone"
        else search_collections(slug="test" if kind == "ssr_slug" else None)
    )
    result = await provider(source).search("test", 10, 1)
    assert result["status"] == "error"
    assert result["items"] == [] and result["returned"] == 0
    assert result["error"]["code"] == "parser_drift"
    assert any("source URL" in w for w in result["warnings"])


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["inside", "outside", "wrong_sku"])
async def test_search_does_not_choose_first_ambiguous_or_wrong_variant_link(scope):
    payload = {"type": "offer", "oskuId": "123", "marketSku": "456", "title": "Test", "price": 100}
    anchors = '<a href="/card/first/123?offer=a">A</a><a href="/card/second/123?offer=b">B</a>'
    if scope == "wrong_sku":
        anchors = '<a href="/card/other/123?sku=789">Other variant</a>'
    source = zone_html(payload, anchors if scope == "inside" else "")
    if scope != "inside":
        source += anchors
    result = await provider(source).search("test", 10, 1)
    assert result["status"] == "error" and result["items"] == []
    assert result["error"]["code"] == "parser_drift"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["ld+json", "ssr"])
async def test_explicit_source_product_url_is_not_replaced_by_card_variant(kind):
    exact = "https://market.yandex.ru/product/123?sku=456&offer=a%2Bb"
    source = (
        '<script type="application/ld+json">'
        + json.dumps({"@type": "ItemList", "itemListElement": [{"item": {"url": exact, "name": "Test"}}]})
        + "</script>"
        if kind == "ld+json"
        else search_collections(url=exact)
    )
    source += '<a href="/card/another-variant/123?sku=789">Other variant</a>'
    result = await provider(source).search("test", 10, 1)
    assert result["status"] == "ok"
    assert result["items"][0]["url"] == exact


@pytest.mark.asyncio
@pytest.mark.parametrize("source_field", ["ld+json", "ld+json_query", "zone"])
async def test_zone_explicit_urls_are_matched_to_correct_sku_not_product_only(source_field):
    items = []
    source = ""
    expected = []
    for sku in ("456", "789"):
        exact = f"https://market.yandex.ru/card/variant-{sku}/123"
        if source_field == "ld+json_query":
            exact += f"?sku={sku}&offer=keep%2B{sku}"
        payload = {"type": "offer", "oskuId": "123", "marketSku": sku, "title": "Test", "price": 100}
        if source_field == "zone":
            payload["url"] = exact
        else:
            items.append({"item": {"url": exact, "name": "Test", "sku": sku}})
        source += zone_html(payload)
        expected.append((sku, exact))
    if items:
        source += (
            '<script type="application/ld+json">'
            + json.dumps({"@type": "ItemList", "itemListElement": items})
            + "</script>"
        )
    result = await provider(source).search("test", 10, 1)
    assert result["status"] == "ok"
    assert [(p["sku_id"], p["url"]) for p in result["items"]] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("source_kind", ["anchor", "payload", "anchor_with_ld"])
async def test_zone_distinct_offers_with_same_sku_keep_their_own_links(source_kind):
    source = ""
    for offer in ("a", "b"):
        payload = {
            "type": "offer",
            "oskuId": "123",
            "marketSku": "456",
            "wareId": offer,
            "title": "Test",
            "price": 100,
        }
        url = URL + f"?sku=456&offer={offer}"
        if source_kind == "payload":
            payload["url"] = url
        source += zone_html(payload, "" if source_kind == "payload" else f'<a href="{url}">Test</a>')
    if source_kind == "anchor_with_ld":
        source += (
            '<script type="application/ld+json">'
            + json.dumps(
                {
                    "@type": "ItemList",
                    "itemListElement": [{"item": {"url": URL, "name": "Family", "sku": "456"}}],
                }
            )
            + "</script>"
        )
    result = await provider(source).search("test", 10, 1)
    assert result["status"] == "ok"
    assert [p["url"] for p in result["items"]] == [URL + f"?sku=456&offer={offer}" for offer in ("a", "b")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source_kind", ["scoped_other_variant", "unscoped_product_only", "ldjson_product_only"]
)
async def test_missing_variant_url_is_skipped_instead_of_borrowing_product_family_link(source_kind):
    payload = {"type": "offer", "oskuId": "123", "marketSku": "456", "title": "Missing", "price": 100}
    good_payload = {**payload, "marketSku": "789", "title": "Good"}
    source = zone_html(payload)
    good_url = "https://market.yandex.ru/card/good/123?offer=good"
    source += zone_html(good_payload, '<a href="/card/good/123?offer=good">Good</a>')
    if source_kind == "unscoped_product_only":
        source += '<a href="/card/generic/123">Generic family link</a>'
    if source_kind == "ldjson_product_only":
        source += (
            '<script type="application/ld+json">'
            + json.dumps({"@type": "ItemList", "itemListElement": [{"item": {"url": URL, "name": "Family"}}]})
            + "</script>"
        )
    result = await provider(source).search("test", 10, 1)
    assert result["status"] == "partial"
    assert [(p["sku_id"], p["url"]) for p in result["items"]] == [("789", good_url)]
    assert any("source URL" in w for w in result["warnings"])


@pytest.mark.asyncio
@pytest.mark.parametrize("source_kind", ["anchor_sku", "anchor_product", "payload_sku"])
async def test_snippet_source_url_must_match_its_product_and_sku(source_kind):
    payload = {"type": "offer", "oskuId": "123", "marketSku": "456", "title": "Test", "price": 100}
    wrong = "/card/other/999" if source_kind == "anchor_product" else "/card/test-kettle/123?sku=789"
    anchors = f'<a href="{wrong}">Other</a>'
    if source_kind == "payload_sku":
        payload["url"] = wrong
        anchors = ""
    result = await provider(zone_html(payload, anchors)).search("test", 10, 1)
    assert result["status"] == "error" and result["items"] == []
    assert result["error"]["code"] == "parser_drift"


def ssr_offer_search(rows):
    collections = {"product": {"123": {"titles": {"raw": "Test"}}}}
    for index, (sku, place_url, offer_url) in enumerate(rows):
        key = str(index)
        collections.setdefault("productShowPlace", {})[key] = {
            "productId": 123,
            "productSnippetId": key,
            "defaultOfferShowPlaceId": key,
            "urls": {"direct": place_url},
        }
        collections.setdefault("offerShowPlace", {})[key] = {"offerId": key}
        collections.setdefault("offer", {})[key] = {"skuId": sku, "url": offer_url}
        collections.setdefault("productSnippet", {})[key] = {"productPayload": {"title": {"value": "Test"}}}
    return (
        '<noframes data-apiary="patch">'
        + json.dumps({"widgets": {"w": {"p": {"options": {"collections": collections}}}}})
        + "</noframes>"
    )


@pytest.mark.asyncio
async def test_ssr_variant_cannot_borrow_explicit_url_from_another_offer():
    exact = "https://market.yandex.ru/product/123"
    source = ssr_offer_search([("456", exact, None), ("789", None, None)])
    result = await provider(source).search("test", 10, 1)
    assert result["status"] == "partial"
    assert [(p["sku_id"], p["url"]) for p in result["items"]] == [("456", exact)]


@pytest.mark.asyncio
async def test_ssr_offer_source_urls_are_reused_exactly_for_each_sku():
    exact = [URL + f"?sku={sku}&offer=keep%2B{sku}" for sku in ("456", "789")]
    source = ssr_offer_search([(sku, None, url) for sku, url in zip(("456", "789"), exact, strict=True)])
    result = await provider(source).search("test", 10, 1)
    assert result["status"] == "ok"
    assert [(p["sku_id"], p["url"]) for p in result["items"]] == list(zip(("456", "789"), exact, strict=True))


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/product/123", "/product--test/123"])
async def test_actual_observed_product_route_is_allowed_without_explicit_json_url(path):
    payload = {"type": "offer", "oskuId": "123", "marketSku": "456", "title": "Test", "price": 100}
    exact = "https://market.yandex.ru" + path + "?sku=456&offer=a%2Bb"
    source = zone_html(payload) + f'<a href="{exact}">Test</a>'
    result = await provider(source).search("test", 10, 1)
    assert result["status"] == "ok" and result["items"][0]["url"] == exact


@pytest.mark.asyncio
async def test_search_values_drift_not_successful_blank_products():
    source = (
        '<script type="application/ld+json">'
        + json.dumps({"@type": "ItemList", "itemListElement": [{"item": {"url": URL}}]})
        + "</script>"
    )
    result = await provider(source).search("test", 10, 1)
    assert result["status"] == "error" and result["error"]["code"] == "parser_drift"


@pytest.mark.asyncio
async def test_search_pagination_unknown_unless_explicit_flag():
    source = card_html() + patch({"visibleSearchResult": {"a": {"page": 1, "total": 10}}})
    # Card fixture supplies a product via ldjson ItemList for the search fallback.
    source += (
        '<script type="application/ld+json">'
        + json.dumps({"@type": "ItemList", "itemListElement": [{"item": {"url": URL, "name": "Test"}}]})
        + "</script>"
    )
    result = await provider(source).search("test", 10, 1)
    assert result["has_next_page"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["search", "get_card"])
async def test_malformed_ssr_shape_is_parser_drift(method):
    from marketplace_mcp.errors import MarketplaceError

    p = provider('<script type="application/ld+json">[]</script>')
    with pytest.raises(MarketplaceError) as caught:
        await p.get_card(URL) if method == "get_card" else await p.search("test", 10, 1)
    assert caught.value.code == "parser_drift"


@pytest.mark.asyncio
async def test_unsafe_search_urls_are_not_misreported_as_legitimate_empty():
    source = (
        '<script type="application/ld+json">'
        + json.dumps(
            {
                "@type": "ItemList",
                "itemListElement": [{"item": {"url": "https://evil.test/card/a/123", "name": "Test"}}],
            }
        )
        + "</script>"
    )
    result = await provider(source).search("test", 10, 1)
    assert result["status"] == "error" and result["error"]["code"] == "parser_drift"


@pytest.mark.asyncio
async def test_unknown_review_text_and_vote_counts_are_null_not_invented_zero():
    result = await provider(card_html()).get_card(URL, True, 1)
    review = result["product"]["reviews"][0]
    assert review["author"] is None and review["date"] is None
    assert review["votes_up"] is None and review["votes_down"] is None
    assert review["comment"] == "test"
