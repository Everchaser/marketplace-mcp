"""Synthetic fixtures reflect observed VARUS fields; not a live fallback."""

import importlib.util
import json

import pytest

from marketplace_mcp.core import MarketplaceService

URL = "https://varus.ua/sample-product"


@pytest.fixture
def provider_module():
    assert importlib.util.find_spec("marketplace_mcp.varus"), "VARUS provider missing"
    from marketplace_mcp import varus

    return varus


def shop(id=1, tms=1001, lat=48.4, lon=35.0, **more):
    return {
        "id": id,
        "tms_id": str(tms),
        "lat": str(lat),
        "long": str(lon),
        "status": "1",
        "is_enabled": "1",
        "is_darkstore": "0",
        "is_darkhub": "0",
        "is_shipping_hub": False,
        "inactive_reason": None,
        "address": "PUBLIC SAMPLE",
        **more,
    }


def html(stock=True, unit="pc", entries=None):
    product = {
        "id": 99,
        "sku": "2656669",
        "name": "Sample",
        "url_key": "sample-product",
        "unit": unit,
        "shop_availability_list": entries
        if entries is not None
        else [{"id": 7, "tms_id": 1001, "product_in_stock": stock, "price": 124, "special_price": 74.9}],
    }
    return (
        '<link rel="canonical" href="'
        + URL
        + '"><script>window.__INITIAL_STATE__='
        + json.dumps({"product": {"current": product}})
        + ";</script>"
    )


class Source:
    def __init__(self, page=None, shops=None):
        self.page = page or html()
        self.shops = shops or [shop()]
        self.calls = []

    async def fetch(self, url):
        self.calls.append(url)
        return (
            json.dumps({"hits": self.shops, "total": {"value": len(self.shops), "relation": "eq"}})
            if "tms_shop/_search" in url
            else self.page
        )


async def test_card_selects_nearest_eligible_shop_and_uses_tms_identity(provider_module):
    source = Source(shops=[shop(2, 1002, 48.401, is_darkstore="1"), shop(1, 1001), shop(3, 1003, 49)])
    service = MarketplaceService(
        {"varus": provider_module.VarusProvider(source, {"coordinates": {"lat": 48.4, "lon": 35}})}
    )
    r = await service.get_card(URL, "varus")
    assert r["status"] == "ok", r
    assert r["product"]["sku"] == "2656669"
    assert r["shop"]["id"] == 1 and r["shop"]["tms_id"] == "1001"
    assert r["shop"]["selection"] == "nearest_straight_line"
    assert r["product"]["availability"]["state"] == "available"
    assert r["product"]["availability"]["source_shop_id"] == 7
    assert r["product"]["availability"]["exact_quantity"] is None
    assert r["product"]["prices"] == {
        "currency": "UAH",
        "ordinary": "124",
        "promotional": "74.9",
        "basis": "unit",
    }


@pytest.mark.parametrize("stock,expected", [(False, "unavailable"), (None, "unknown"), ("0", "unknown")])
async def test_boolean_availability_only(provider_module, stock, expected):
    s = MarketplaceService({"varus": provider_module.VarusProvider(Source(html(stock)), {"shop_id": 1})})
    r = await s.get_card(URL, "varus")
    assert r["product"]["availability"]["state"] == expected


async def test_unconfigured_is_structured_no_default_shop(provider_module):
    src = Source()
    r = await MarketplaceService({"varus": provider_module.VarusProvider(src, {})}).get_card(URL, "varus")
    assert r["status"] == "error" and r["error"]["code"] == "location_unconfigured"
    assert not src.calls


async def test_non_json_ssr_never_executes(provider_module):
    r = await MarketplaceService(
        {"varus": provider_module.VarusProvider(Source("window.__INITIAL_STATE__=alert(1)"), {"shop_id": 1})}
    ).get_card(URL, "varus")
    assert r["status"] == "error" and r["error"]["code"] == "extraction_failed"


async def test_absent_shop_is_unknown_not_unavailable(provider_module):
    r = await MarketplaceService(
        {"varus": provider_module.VarusProvider(Source(html(entries=[])), {"shop_id": 1})}
    ).get_card(URL, "varus")
    assert r["product"]["availability"]["state"] == "unknown"
    assert r["product"]["prices"]["ordinary"] is None


async def test_search_observes_canonical_and_joins_catalogue_identity(provider_module):
    class SearchSource(Source):
        async def fetch(self, url):
            if "product_v2/_search" in url:
                self.calls.append(url)
                return json.dumps(
                    {
                        "hits": [{"id": 99, "sku": "2656669", "name": "Sample", "url_key": "sample-product"}],
                        "total": {"value": 1, "relation": "eq"},
                    }
                )
            return await super().fetch(url)

    src = SearchSource()
    r = await MarketplaceService({"varus": provider_module.VarusProvider(src, {"shop_id": 1})}).search(
        "sample", "varus", 2
    )
    assert r["status"] == "ok", r
    assert r["returned"] == 1 and r["items"][0]["url"] == URL
    assert r["items"][0]["url_provenance"] == "observed_card_canonical_and_ssr_identity"
    assert r["shop"]["id"] == 1
    assert r["has_next_page"] is False
    assert any("product_v2/_search" in u for u in src.calls)


async def test_search_rejects_catalogue_card_identity_mismatch(provider_module):
    class SearchSource(Source):
        async def fetch(self, url):
            if "product_v2/_search" in url:
                return json.dumps(
                    {
                        "hits": [{"id": 999, "sku": "wrong", "url_key": "sample-product"}],
                        "total": {"value": 1, "relation": "eq"},
                    }
                )
            return await super().fetch(url)

    r = await MarketplaceService(
        {"varus": provider_module.VarusProvider(SearchSource(), {"shop_id": 1})}
    ).search("sample", "varus")
    assert r["status"] == "partial" and not r["items"]
    assert r["warnings"]


async def test_public_geocoding_has_attribution_without_address_echo(provider_module):
    class Geo:
        async def resolve(self, config):
            return (48.4, 35.0)

    config = {
        "address": "non-confidential-public-sample",
        "geocoder": "nominatim",
        "public_address_consent": True,
    }
    r = await MarketplaceService({"varus": provider_module.VarusProvider(Source(), config, Geo())}).get_card(
        URL, "varus"
    )
    assert r["status"] == "ok"
    assert r["shop"]["geocoding"]["attribution"] == "© OpenStreetMap contributors (ODbL)"
    assert "non-confidential-public-sample" not in json.dumps(r)


async def test_explicitly_inactive_card_shop_does_not_claim_available(provider_module):
    page = html(
        entries=[
            {
                "id": 7,
                "tms_id": 1001,
                "product_in_stock": True,
                "price": 124,
                "special_price": 74.9,
                "is_enabled": "0",
            }
        ]
    )
    r = await MarketplaceService(
        {"varus": provider_module.VarusProvider(Source(page), {"shop_id": 1})}
    ).get_card(URL, "varus")
    assert r["product"]["availability"]["state"] == "unknown"
    assert r["product"]["prices"]["ordinary"] is None


async def test_search_candidate_error_code_is_preserved(provider_module):
    from marketplace_mcp.errors import MarketplaceError

    class SearchSource(Source):
        async def fetch(self, url):
            if "product_v2/_search" in url:
                return json.dumps(
                    {
                        "hits": [{"id": 99, "sku": "2656669", "url_key": "sample-product"}],
                        "total": {"value": 1, "relation": "eq"},
                    }
                )
            if url == URL:
                raise MarketplaceError("captcha", "challenge")
            return await super().fetch(url)

    r = await MarketplaceService(
        {"varus": provider_module.VarusProvider(SearchSource(), {"shop_id": 1})}
    ).search("sample", "varus")
    assert r["status"] == "partial" and r["candidate_errors"][0]["error"]["code"] == "captcha"


async def test_card_reports_transport_observation_not_parse_time(provider_module):
    class MetadataSource(Source):
        async def fetch_document(self, url):
            return {
                "text": self.page,
                "fetched_at": "2026-10-09T12:00:00+00:00",
                "upstream_age_seconds": 955,
                "upstream_cache_stale": True,
                "cache_ttl_seconds": 60,
            }

    r = await MarketplaceService(
        {"varus": provider_module.VarusProvider(MetadataSource(), {"shop_id": 1})}
    ).get_card(URL, "varus")
    assert r["product"]["availability"]["observed_at"] == "2026-10-09T12:00:00+00:00"
    assert r["product"]["observation"]["upstream_cache_stale"] is True


async def test_catalogue_name_filter_casefolds_user_query(provider_module):
    from urllib.parse import parse_qs, urlsplit

    class SearchSource(Source):
        async def fetch(self, url):
            if "product_v2/_search" in url:
                self.calls.append(url)
                return json.dumps({"hits": [], "total": {"value": 0, "relation": "eq"}})
            return await super().fetch(url)

    src = SearchSource()
    r = await MarketplaceService({"varus": provider_module.VarusProvider(src, {"shop_id": 1})}).search(
        "БАНАН", "varus"
    )
    assert r["query"] == "БАНАН"
    url = next(u for u in src.calls if "product_v2" in u)
    query = json.loads(parse_qs(urlsplit(url).query)["request"][0])
    assert query["_appliedFilters"][0]["value"]["like"] == "банан"


async def test_multiterm_catalogue_search_uses_observed_and_filters(provider_module):
    from urllib.parse import parse_qs, urlsplit

    class SearchSource(Source):
        async def fetch(self, url):
            if "product_v2/_search" in url:
                self.calls.append(url)
                return json.dumps({"hits": [], "total": {"value": 0, "relation": "eq"}})
            return await super().fetch(url)

    src = SearchSource()
    await MarketplaceService({"varus": provider_module.VarusProvider(src, {"shop_id": 1})}).search(
        "Пиріжок здобний", "varus"
    )
    url = next(u for u in src.calls if "product_v2" in u)
    request = json.loads(parse_qs(urlsplit(url).query)["request"][0])
    assert [f["value"]["like"] for f in request["_appliedFilters"] if f["attribute"] == "name"] == [
        "пиріжок",
        "здобний",
    ]


async def test_zero_special_price_is_observed_no_promotion_sentinel(provider_module):
    page = html(
        entries=[
            {
                "id": 1,
                "tms_id": 1001,
                "product_in_stock": True,
                "price": 74.9,
                "special_price": 0,
                "discount_percent": 0,
            }
        ],
        unit="kg",
    )
    r = await MarketplaceService(
        {"varus": provider_module.VarusProvider(Source(page), {"shop_id": 1})}
    ).get_card(URL, "varus")
    assert r["product"]["prices"]["ordinary"] == "74.9"
    assert r["product"]["prices"]["promotional"] is None


async def test_observed_ordinary_zero_is_not_dropped(provider_module):
    page = html(
        entries=[{"id": 1, "tms_id": 1001, "product_in_stock": True, "price": 0, "special_price": None}]
    )
    r = await MarketplaceService(
        {"varus": provider_module.VarusProvider(Source(page), {"shop_id": 1})}
    ).get_card(URL, "varus")
    assert r["product"]["prices"]["ordinary"] == "0"


async def test_duplicate_tms_identity_fails_even_with_shop_override(provider_module):
    src = Source(shops=[shop(1, 1001), shop(3, 1001, 49)])
    r = await MarketplaceService({"varus": provider_module.VarusProvider(src, {"shop_id": 1})}).get_card(
        URL, "varus"
    )
    assert r["status"] == "error" and r["error"]["code"] == "shop_extraction_failed"


@pytest.mark.parametrize("wrapper", ["<!-- {} -->", "<div>{}</div>"])
def test_ssr_ignores_forged_state_outside_scripts(provider_module, wrapper):
    actual = html()
    state = json.loads(actual.split("window.__INITIAL_STATE__=", 1)[1].split(";</script>", 1)[0])
    state["product"]["current"]["sku"] = "FORGED"
    forged = "window.__INITIAL_STATE__=" + json.dumps(state)
    product, _ = provider_module.parse_card(wrapper.format(forged) + actual, URL)
    assert product["sku"] == "2656669"


@pytest.mark.parametrize(
    "script",
    [
        "window.__INITIAL_STATE__={state};window.__INITIAL_STATE__={state};",
        "/* window.__INITIAL_STATE__={state}; */",
        "const text = 'window.__INITIAL_STATE__={state};';",
        "if (false) {{ window.__INITIAL_STATE__={state}; }}",
    ],
)
def test_ssr_requires_supported_unambiguous_script_assignment(provider_module, script):
    from marketplace_mcp.errors import MarketplaceError

    state = html().split("window.__INITIAL_STATE__=", 1)[1].split(";</script>", 1)[0]
    page = '<link rel="canonical" href="' + URL + '"><script>' + script.format(state=state) + "</script>"
    with pytest.raises(MarketplaceError, match="could not be verified"):
        provider_module.parse_card(page, URL)


def test_ssr_rejects_multiple_script_states(provider_module):
    from marketplace_mcp.errors import MarketplaceError

    with pytest.raises(MarketplaceError, match="could not be verified"):
        provider_module.parse_card(html() + html(), URL)


@pytest.mark.parametrize("field", ["id", "sku", "name", "url_key"])
@pytest.mark.parametrize("value", [None, True, False, {}, {"x": 1}, [], ["Sample"], "", " "])
async def test_card_rejects_malformed_required_identity(provider_module, field, value):
    state = json.loads(html().split("window.__INITIAL_STATE__=", 1)[1].split(";</script>", 1)[0])
    state["product"]["current"][field] = value
    page = (
        html().split("<script>", 1)[0]
        + "<script>window.__INITIAL_STATE__="
        + json.dumps(state)
        + ";</script>"
    )
    service = MarketplaceService({"varus": provider_module.VarusProvider(Source(page), {"shop_id": 1})})
    result = await service.get_card(URL, "varus")
    assert result["status"] == "error" and result["error"]["code"] == "extraction_failed", result


@pytest.mark.parametrize("field", ["id", "sku"])
@pytest.mark.parametrize("value", [0, -1, 1.5, "0", "-1", "1.5", "１２３", "9" * 20, 2**63])
async def test_card_rejects_unbounded_or_nonpositive_identity(provider_module, field, value):
    state = json.loads(html().split("window.__INITIAL_STATE__=", 1)[1].split(";</script>", 1)[0])
    state["product"]["current"][field] = value
    page = (
        html().split("<script>", 1)[0]
        + "<script>window.__INITIAL_STATE__="
        + json.dumps(state)
        + ";</script>"
    )
    result = await MarketplaceService(
        {"varus": provider_module.VarusProvider(Source(page), {"shop_id": 1})}
    ).get_card(URL, "varus")
    assert result["status"] == "error" and result["error"]["code"] == "extraction_failed", result


async def test_missing_id_cannot_produce_successful_card_or_complete_total(provider_module):
    state = json.loads(html().split("window.__INITIAL_STATE__=", 1)[1].split(";</script>", 1)[0])
    del state["product"]["current"]["id"]
    page = (
        html().split("<script>", 1)[0]
        + "<script>window.__INITIAL_STATE__="
        + json.dumps(state)
        + ";</script>"
    )
    service = MarketplaceService({"varus": provider_module.VarusProvider(Source(page), {"shop_id": 1})})
    result = await service.get_cards([URL], "varus", quantities=["1"])
    assert result["status"] == "error", result
    assert result["results"][0]["error"]["code"] == "extraction_failed"
    assert result["estimate"]["ordinary"]["complete"] is False
    assert result["estimate"]["ordinary"]["included"] == 0


@pytest.mark.parametrize("container", ["template", "noscript"])
async def test_inert_only_state_is_excluded_from_estimate(provider_module, container):
    canonical, script = html().split("<script>", 1)
    forged = "<script>" + script.replace('"2656669"', '"1234567"')
    page = canonical + f"<{container}>" + forged + f"</{container}>"
    service = MarketplaceService({"varus": provider_module.VarusProvider(Source(page), {"shop_id": 1})})
    result = await service.get_cards([URL], "varus", quantities=["1"])
    assert result["status"] == "error", result
    assert result["results"][0]["error"]["code"] == "extraction_failed"
    assert result["estimate"]["ordinary"]["complete"] is False
    assert result["estimate"]["ordinary"]["included"] == 0


@pytest.mark.parametrize(
    "container", ["title", "textarea", "xmp", "iframe", "noembed", "noframes", "plaintext"]
)
async def test_rawtext_only_state_is_excluded_from_estimate(provider_module, container):
    canonical, script = html().split("<script>", 1)
    forged = "<script>" + script.replace('"2656669"', '"1234567"')
    page = canonical + f"<{container}>" + forged + f"</{container}>"
    service = MarketplaceService({"varus": provider_module.VarusProvider(Source(page), {"shop_id": 1})})
    result = await service.get_cards([URL], "varus", quantities=["1"])
    assert result["status"] == "error", result
    assert result["results"][0]["error"]["code"] == "extraction_failed"
    assert result["estimate"]["ordinary"]["complete"] is False
    assert result["estimate"]["ordinary"]["included"] == 0


@pytest.mark.parametrize(
    "container",
    ["template", "noscript", "style", "title", "textarea", "xmp", "iframe", "noembed", "noframes"],
)
@pytest.mark.parametrize("inert_first", [True, False])
def test_inert_state_cannot_shadow_actual(provider_module, container, inert_first):
    canonical, script = html().split("<script>", 1)
    actual = "<script>" + script
    forged = actual.replace('"2656669"', '"1234567"')
    inert = f"<{container}>" + forged + f"</{container}>"
    page = canonical + (inert + actual if inert_first else actual + inert)
    product, _ = provider_module.parse_card(page, URL)
    assert product["sku"] == "2656669"


@pytest.mark.parametrize(
    "wrapper",
    [
        "<template><template>{}</template></template>",
        "<template><div>{}</div></template>",
        "<template/>{}</template>",
        "<noscript/>{}</noscript>",
        "<textarea/>{}</textarea>",
        "<template><textarea></template>{}</textarea></template>",
        "<template><noscript></template>{}</noscript></template>",
        "<noscript><template>{}</template></noscript>",
    ],
)
def test_nested_and_selfclosing_inert_contexts_do_not_shadow_actual(provider_module, wrapper):
    actual = html()
    forged = actual.replace('"2656669"', '"1234567"')
    product, _ = provider_module.parse_card(wrapper.format(forged) + actual, URL)
    assert product["sku"] == "2656669"


@pytest.mark.parametrize(
    "opening", ["<template>", "<template/>", "<noscript>", "<noscript/>", "<textarea>", "<plaintext>"]
)
def test_unclosed_inert_context_never_yields_state(provider_module, opening):
    from marketplace_mcp.errors import MarketplaceError

    with pytest.raises(MarketplaceError, match="could not be verified"):
        provider_module.parse_card(html().split("<script>", 1)[0] + opening + html(), URL)


def test_plaintext_end_tag_does_not_restore_executable_markup(provider_module):
    from marketplace_mcp.errors import MarketplaceError

    with pytest.raises(MarketplaceError, match="could not be verified"):
        provider_module.parse_card("<plaintext></plaintext>" + html(), URL)


@pytest.mark.parametrize("closing", ["</div>", "</body>", "</html>", "</table>"])
async def test_unrelated_end_tag_cannot_exit_template(provider_module, closing):
    canonical, script = html().split("<script>", 1)
    forged = "<script>" + script.replace('"2656669"', '"1234567"')
    page = canonical + "<template>" + closing + forged
    service = MarketplaceService({"varus": provider_module.VarusProvider(Source(page), {"shop_id": 1})})
    result = await service.get_cards([URL], "varus", quantities=["1"])
    assert result["status"] == "error", result
    assert result["results"][0]["error"]["code"] == "extraction_failed"
    assert result["estimate"]["ordinary"]["included"] == 0


async def test_card_rejects_unbounded_title(provider_module):
    page = html().replace('"name": "Sample"', '"name": "' + "x" * 1001 + '"')
    result = await MarketplaceService(
        {"varus": provider_module.VarusProvider(Source(page), {"shop_id": 1})}
    ).get_card(URL, "varus")
    assert result["status"] == "error" and result["error"]["code"] == "extraction_failed", result


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("config", [{"shop_id": 1}, {"coordinates": {"lat": 48.4, "lon": 35}}])
@pytest.mark.parametrize("duplicate_flags", [{}, {"is_enabled": "0"}])
async def test_duplicate_physical_shop_identity_fails_closed(
    provider_module, reverse, config, duplicate_flags
):
    shops = [shop(1, 1001), shop(1, 1002, 49, **duplicate_flags)]
    if reverse:
        shops.reverse()
    result = await MarketplaceService(
        {"varus": provider_module.VarusProvider(Source(shops=shops), config)}
    ).get_card(URL, "varus")
    assert result["status"] == "error" and result["error"]["code"] == "shop_extraction_failed", result
