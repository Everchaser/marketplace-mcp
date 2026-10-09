"""Batch fallback uses synthetic SSR fixtures, never live substitute data."""

import pytest
from test_varus import URL, Source, html, shop

from marketplace_mcp.core import MarketplaceService
from marketplace_mcp.varus import SHOPS_URL, VarusProvider


def service(source, config=None):
    return MarketplaceService({"varus": VarusProvider(source, config or {"shop_id": 1})})


async def test_primary_complete_stops_and_reuses_duplicate_card():
    source = Source(shops=[shop(), shop(2, 1002, 48.41)])
    result = await service(source).get_cards([URL, URL], "varus")
    assert result["primary_shop"]["id"] == 1
    assert [s["id"] for s in result["attempted_shops"]] == [1]
    assert result["available"] == 2 and result["unresolved"] == []
    assert source.calls.count(SHOPS_URL) == source.calls.count(URL) == 1
    assert [r["url"] for r in result["results"]] == [URL, URL]


async def test_missing_line_uses_second_shop_and_preserves_primary_evidence():
    source = Source(
        html(
            entries=[
                {"tms_id": 1001, "product_in_stock": False, "price": 10},
                {"tms_id": 1002, "product_in_stock": True, "price": 12},
            ]
        ),
        [shop(), shop(2, 1002, 48.41), shop(3, 1003, 48.42)],
    )
    result = await service(source).get_cards([URL], "varus")
    assert result["results"][0]["shop"]["id"] == 2
    assert result["primary_shop"]["id"] == 1
    assert [s["id"] for s in result["attempted_shops"]] == [1, 2]
    line = result["results"][0]
    assert line["primary_observation"]["product"]["availability"]["state"] == "unavailable"
    assert [a["availability"]["state"] for a in line["shop_attempts"]] == ["unavailable", "available"]
    assert line["product"]["prices"]["ordinary"] == "12"
    assert source.calls.count(SHOPS_URL) == source.calls.count(URL) == 1


async def test_original_invalid_error_survives_catalogue_failure():
    source = Source()
    result = await service(source, {"shop_id": 999}).get_cards(["https://example.org/private", URL], "varus")
    assert result["results"][0]["error"]["code"] == "validation_error"
    assert result["results"][1]["error"]["code"] == "shop_unavailable"
    assert result["primary_shop"] is None and result["attempted_shops"] == []
    assert result["unresolved"] == [
        {"index": 0, "reason": "validation_error"},
        {"index": 1, "reason": "shop_unavailable"},
    ]
    assert URL not in source.calls


async def test_all_invalid_urls_do_not_fetch_catalogue():
    source = Source()
    result = await service(source).get_cards(["https://example.org/private", None, {"url": URL}], "varus")
    assert result["failed"] == 3 and result["returned"] == 0
    assert all(r["error"]["code"] == "validation_error" for r in result["results"])
    assert not source.calls


def entry(tms, state=True, price=10, promo=0, **more):
    return {"tms_id": tms, "product_in_stock": state, "price": price, "special_price": promo, **more}


class Pages(Source):
    def __init__(self, entries_by_path, shops):
        super().__init__(shops=shops)
        self.pages = {
            URL.replace("sample-product", path): html(entries=entries).replace("sample-product", path)
            for path, entries in entries_by_path.items()
        }

    async def fetch(self, url):
        if url in self.pages:
            self.calls.append(url)
            return self.pages[url]
        return await super().fetch(url)


async def test_verified_allocations_allow_mixed_totals_but_default_estimator_rejects():
    from marketplace_mcp.estimates import estimate, validate_quantities

    source = Pages(
        {
            "sample-product": [entry(1001, price="1.125", promo="0.5"), entry(1002, price=1, promo="0.25")],
            "second-product": [entry(1001, False), entry(1002, price="2.25")],
        },
        [shop(), shop(2, 1002, 48.41)],
    )
    urls = [URL, URL.replace("sample-product", "second-product"), URL]
    quantities = ["2", "3", "1"]
    result = await service(source).get_cards(urls, "varus", quantities=quantities)
    assert result["estimate"]["ordinary"] == {
        "total": "10.13",
        "included": 3,
        "excluded": 0,
        "complete": True,
    }
    assert result["estimate"]["promotional"] == {
        "total": "1.50",
        "included": 2,
        "excluded": 1,
        "complete": False,
    }
    assert [(g["shop"]["id"], g["indices"], g["count"]) for g in result["allocations"]] == [
        (1, [0, 2], 2),
        (2, [1], 1),
    ]
    assert [s["ordinary"]["total"] for s in result["estimate"]["per_shop"]] == ["3.38", "6.75"]
    assert [s["promotional"]["included"] for s in result["estimate"]["per_shop"]] == [2, 0]
    assert result["results"][0]["shop"]["id"] == 1  # never chase a lower price/promotion
    ordinary = estimate(result["results"], validate_quantities(urls, quantities, None), "varus")
    assert ordinary["ordinary"]["included"] == 0
    assert {line["excluded_reason"] for line in ordinary["lines"]} == {"shop_mismatch"}


@pytest.mark.parametrize("limit", [0, 1, 2, 3])
async def test_global_limit_and_no_match_points_are_counted(limit):
    source = Pages(
        {
            "sample-product": [entry(1001, False), entry(1003, False), entry(1004, False), entry(1005)],
            "second-product": [entry(1001, False), entry(1002), entry(1003)],
        },
        [shop(i, 1000 + i, 48.4 + i / 100) for i in range(1, 6)],
    )
    result = await service(source).get_cards(
        [URL, URL.replace("sample-product", "second-product"), URL], "varus", max_extra_shops=limit
    )
    assert [s["id"] for s in result["attempted_shops"]] == list(range(1, limit + 2))
    assert result["fallback"]["stop_reason"] == "extra_shop_limit"
    assert result["fallback"]["extra_shops_inspected"] == limit
    assert result["available"] == (1 if limit else 0)
    assert result["returned"] == 3 and result["failed"] == 0
    assert result["results"][0]["shop"]["id"] == 1
    assert len(result["results"][0]["shop_attempts"]) == limit + 1
    if limit:
        assert result["results"][0]["shop_attempts"][1]["availability"]["state"] == "unknown"
        assert result["attempted_shops"][1]["checked_indices"] == [0, 1, 2]
        assert result["results"][1]["shop"]["id"] == 2
        assert len(result["results"][1]["shop_attempts"]) == 2
    assert source.calls.count(SHOPS_URL) == 1 and len(source.calls) == 3


async def test_remaining_lines_fill_third_and_fourth_without_rechecking_satisfied():
    source = Pages(
        {
            "sample-product": [entry(1001, price=5)],
            "second-product": [entry(1001, False), entry(1002, False), entry(1003, price=7)],
            "third-product": [
                entry(1001, None),
                entry(1002, False),
                entry(1003, False),
                entry(1004, price=11),
            ],
        },
        [shop(i, 1000 + i, 48.4 + i / 100) for i in range(1, 6)],
    )
    urls = [
        URL,
        URL.replace("sample-product", "second-product"),
        URL.replace("sample-product", "third-product"),
    ]
    r = await service(source).get_cards(urls, "varus", quantities=["1", "2", "3"])
    assert [s["checked_indices"] for s in r["attempted_shops"]] == [[0, 1, 2], [1, 2], [1, 2], [2]]
    assert [line["shop"]["id"] for line in r["results"]] == [1, 3, 4]
    assert [len(line["shop_attempts"]) for line in r["results"]] == [1, 3, 4]
    assert r["available"] == 3 and r["unresolved"] == []
    assert r["estimate"]["ordinary"]["total"] == "52.00" and r["estimate"]["ordinary"]["complete"]
    assert [group["shop"]["id"] for group in r["allocations"]] == [1, 3, 4]
    assert len(source.calls) == 4


async def test_resolved_public_geocoding_origin_is_retained_without_regeocoding():
    class Geo:
        calls = 0

        async def resolve(self, config):
            self.calls += 1
            return (48.4, 35.0)

    geo = Geo()
    source = Source(html(stock=False), [shop(), shop(2, 1002, 48.41)])
    s = MarketplaceService(
        {
            "varus": VarusProvider(
                source,
                {"address": "PUBLIC SAMPLE", "geocoder": "nominatim", "public_address_consent": True},
                geo,
            )
        }
    )
    r = await s.get_cards([URL, URL], "varus")
    assert geo.calls == 1
    assert r["fallback"]["origin"] == {"lat": 48.4, "lon": 35.0}
    assert r["fallback"]["origin_source"] == "resolved_public_address"
    assert "geocoding" in r["primary_shop"]
    assert source.calls.count(SHOPS_URL) == 1


@pytest.mark.parametrize("value", [True, False, -1, 4, 1.0, "1", None])
async def test_invalid_extra_limit_rejected_before_network(value):
    source = Source()
    result = await service(source).get_cards([URL], "varus", max_extra_shops=value)
    assert result["error"]["code"] == "validation_error"
    assert not source.calls


@pytest.mark.parametrize("available_at", [2, 3, 4, 5, None])
async def test_first_available_rank_and_four_shop_final_cap(available_at):
    source = Source(
        html(entries=[entry(1000 + i, i == available_at) for i in range(1, 6)]),
        [shop(i, 1000 + i, 48.4 + i / 100) for i in reversed(range(1, 6))],
    )
    r = await service(source).get_cards([URL, URL], "varus")
    found = available_at is not None and available_at <= 4
    count = available_at if found else 4
    assert [s["id"] for s in r["attempted_shops"]] == list(range(1, count + 1))
    assert [line["shop"]["id"] for line in r["results"]] == [available_at if found else 1] * 2
    assert r["available"] == (2 if found else 0)
    assert r["fallback"]["stop_reason"] == ("all_valid_lines_available" if found else "extra_shop_limit")
    assert r["returned"] == 2 and r["failed"] == 0
    assert source.calls == [SHOPS_URL, URL]


@pytest.mark.parametrize("count", [1, 2, 3, 4])
async def test_catalogue_exhaustion_reports_actual_distinct_points(count):
    source = Source(html(stock=False), [shop(i, 1000 + i, 48.4 + i / 100) for i in range(1, count + 1)])
    r = await service(source).get_cards([URL], "varus")
    assert len(r["attempted_shops"]) == count
    assert r["fallback"]["stop_reason"] == "catalogue_exhausted"
    assert r["fallback"]["extra_shops_inspected"] == count - 1
    assert r["unresolved"] == [{"index": 0, "reason": "unavailable"}]
    assert r["results"][0]["product"]["availability"]["state"] == "unavailable"
    assert r["allocations"] == []


@pytest.mark.parametrize(
    "coordinates,expected", [(None, [1, 2, 3, 4]), ({"lat": 48.5, "lon": 35}, [1, 4, 3, 2])]
)
async def test_override_primary_and_original_anchor_determine_rank(coordinates, expected):
    config = {"shop_id": 1}
    if coordinates:
        config["coordinates"] = coordinates
    source = Source(
        html(stock=False), [shop(), shop(2, 1002, 48.41), shop(3, 1003, 48.44), shop(4, 1004, 48.48)]
    )
    r = await service(source, config).get_cards([URL], "varus")
    assert r["primary_shop"]["id"] == 1
    assert [s["id"] for s in r["attempted_shops"]] == expected
    assert r["fallback"]["origin"] == (coordinates or {"lat": 48.4, "lon": 35.0})
    assert r["fallback"]["origin_source"] == (
        "configured_coordinates" if coordinates else "primary_shop_coordinates"
    )


@pytest.mark.parametrize(
    "more",
    [
        {"is_darkstore": "1"},
        {"is_darkhub": "1"},
        {"is_shipping_hub": True},
        {"is_enabled": "0"},
        {"status": "0"},
        {"inactive_reason": "closed"},
        {"lat": "bad"},
    ],
)
async def test_ineligible_alternatives_never_count_or_supply_stock(more):
    source = Source(
        html(entries=[entry(1001, False), entry(1002), entry(1003)]),
        [shop(), shop(2, 1002, **({"lat": 48.401} | more)), shop(3, 1003, 48.42)],
    )
    r = await service(source).get_cards([URL], "varus")
    assert [s["id"] for s in r["attempted_shops"]] == [1, 3]
    assert r["results"][0]["shop"]["id"] == 3


@pytest.mark.parametrize("duplicate,reverse", [("id", False), ("id", True), ("tms", False), ("tms", True)])
async def test_ambiguous_alternatives_are_excluded_including_inactive_duplicate(duplicate, reverse):
    records = [
        shop(),
        shop(2, 1002, 48.401),
        shop(2 if duplicate == "id" else 8, 1002 if duplicate == "tms" else 1008, 48.402, is_enabled="0"),
        shop(3, 1003, 48.42),
    ]
    source = Source(
        html(entries=[entry(1001, False), entry(1002), entry(1003)]),
        list(reversed(records)) if reverse else records,
    )
    r = await service(source).get_cards([URL], "varus")
    assert [s["id"] for s in r["attempted_shops"]] == [1, 3]
    assert r["results"][0]["shop"]["id"] == 3


@pytest.mark.parametrize("duplicate", ["id", "tms"])
async def test_ambiguous_primary_remains_failclosed(duplicate):
    records = [
        shop(),
        shop(1 if duplicate == "id" else 8, 1001 if duplicate == "tms" else 1008, 48.402),
        shop(3, 1003, 48.42),
    ]
    source = Source(html(entries=[entry(1001, False), entry(1003)]), records)
    r = await service(source).get_cards([URL], "varus")
    assert r["results"][0]["error"]["code"] == "shop_extraction_failed"
    assert r["primary_shop"] is None and not r["attempted_shops"]
    assert URL not in source.calls


@pytest.mark.parametrize(
    "entries",
    [
        [],
        [entry(1001, None), entry(1002, "0")],
        [entry(1001), entry(1001), entry(1002, True, is_enabled="0")],
    ],
)
async def test_unknown_or_ambiguous_stock_is_preserved_not_absence(entries):
    source = Source(html(entries=entries), [shop(), shop(2, 1002, 48.41)])
    r = await service(source).get_cards([URL], "varus", quantities=["1"])
    assert r["available"] == 0 and r["returned"] == 1
    assert r["unresolved"] == [{"index": 0, "reason": "unknown"}]
    assert [a["availability"]["state"] for a in r["results"][0]["shop_attempts"]] == ["unknown", "unknown"]
    assert r["estimate"]["lines"][0]["excluded_reason"] == "availability_unknown"
    assert r["estimate"]["ordinary"]["included"] == 0


@pytest.mark.parametrize("code", ["transport_error", "captcha", "not_found", "extraction_failed"])
async def test_card_failures_are_original_errors_and_never_shop_checks(code):
    from marketplace_mcp.errors import MarketplaceError

    class Failed(Source):
        async def fetch(self, url):
            if url == URL:
                self.calls.append(url)
                if code == "extraction_failed":
                    return "bad SSR"
                raise MarketplaceError(code, "original failure")
            return await super().fetch(url)

    source = Failed(shops=[shop(), shop(2, 1002, 48.41)])
    r = await service(source).get_cards([URL, URL], "varus", quantities=["1", "2"])
    assert r["failed"] == 2 and r["returned"] == r["available"] == 0
    assert [line["error"]["code"] for line in r["results"]] == [code, code]
    assert r["attempted_shops"] == [] and r["fallback"]["stop_reason"] == "no_valid_cards"
    assert r["estimate"]["ordinary"]["included"] == 0
    assert source.calls == [SHOPS_URL, URL]


async def test_mixed_errors_never_enter_remaining_checks_and_duplicates_keep_order():
    from marketplace_mcp.errors import MarketplaceError

    class Mixed(Pages):
        async def fetch(self, url):
            if url.endswith("broken-product"):
                self.calls.append(url)
                raise MarketplaceError("transport_error", "original failure")
            return await super().fetch(url)

    source = Mixed(
        {"sample-product": [entry(1001)], "second-product": [entry(1001, False), entry(1002)]},
        [shop(), shop(2, 1002, 48.41), shop(3, 1003, 48.42)],
    )
    urls = [
        URL,
        "https://example.org/private",
        URL.replace("sample-product", "second-product"),
        URL.replace("sample-product", "broken-product"),
        URL,
    ]
    r = await service(source).get_cards(urls, "varus", quantities=["1"] * 5)
    assert [line["url"] for line in r["results"]] == urls
    assert r["requested"] == 5 and r["returned"] == 3 and r["failed"] == 2 and r["available"] == 3
    assert [s["checked_indices"] for s in r["attempted_shops"]] == [[0, 2, 4], [2]]
    assert [x["reason"] for x in r["unresolved"]] == ["validation_error", "transport_error"]
    assert r["estimate"]["ordinary"]["included"] == 3 and not r["estimate"]["ordinary"]["complete"]
    assert len(source.calls) == 4 and source.calls.count(URL) == 1


@pytest.mark.parametrize(
    "price,promo,unit,reason",
    [(None, 0, "unit", "missing_price"), (12, 0, "kg", "unit_mismatch"), (None, 5, "unit", None)],
)
async def test_available_primary_never_moves_for_price_or_units(price, promo, unit, reason):
    source = Source(
        html(entries=[entry(1001, price=price, promo=promo), entry(1002, price=10, promo=4)]),
        [shop(), shop(2, 1002, 48.41)],
    )
    r = await service(source).get_cards([URL], "varus", quantities=["1"], units=[unit])
    assert r["results"][0]["shop"]["id"] == 1 and len(r["attempted_shops"]) == 1
    assert r["estimate"]["lines"][0]["excluded_reason"] == reason


async def test_single_card_does_not_fallback():
    source = Source(html(entries=[entry(1001, False), entry(1002)]), [shop(), shop(2, 1002, 48.41)])
    r = await service(source).get_card(URL, "varus")
    assert r["shop"]["id"] == 1 and r["product"]["availability"]["state"] == "unavailable"
    assert "primary_observation" not in r and "shop_attempts" not in r


async def test_allocation_authorization_cannot_be_faked_by_response_flag_or_mismatching_identity():
    from marketplace_mcp.estimates import VerifiedBatchAllocations, estimate, validate_quantities

    source = Pages(
        {"sample-product": [entry(1001)], "second-product": [entry(1001, False), entry(1002)]},
        [shop(), shop(2, 1002, 48.41)],
    )
    urls = [URL, URL.replace("sample-product", "second-product")]
    r = await service(source).get_cards(urls, "varus")
    quantities = validate_quantities(urls, ["1", "1"], None)
    for authorization in [
        True,
        r["allocations"],
        VerifiedBatchAllocations(((0, 1, "1001"), (1, 1, "1001"))),
        VerifiedBatchAllocations(((0, 1, "1001"), (0, 1, "1001"))),
    ]:
        result = estimate(r["results"], quantities, "varus", allocations=authorization)
        assert result["ordinary"]["included"] == 0
    with pytest.raises(ValueError):
        VerifiedBatchAllocations.from_groups(
            r["results"], [{"shop": r["results"][0]["shop"], "indices": [0, 1]}]
        )


@pytest.mark.parametrize("value", [True, False, 1.0, "1", -1, 4, None])
async def test_mcp_schema_strict_extra_limit_cannot_coerce(value):
    from marketplace_mcp.server import create_server

    source = Source()
    server = create_server(service(source))
    with pytest.raises(Exception, match="validation"):
        await server.call_tool("get_cards", {"urls": [URL], "marketplace": "varus", "max_extra_shops": value})
    assert not source.calls


async def test_additive_schema_keeps_other_tools_identical_and_yandex_unchanged():
    from marketplace_mcp.server import create_server

    tools = await create_server(service(Source())).list_tools()
    assert len(tools) == 3
    for tool in tools:
        if tool.name == "get_cards":
            p = tool.inputSchema["properties"]["max_extra_shops"]
            assert p["type"] == "integer" and p["minimum"] == 0 and p["maximum"] == 3 and p["default"] == 3
            assert "max_extra_shops" not in tool.inputSchema.get("required", [])
        else:
            assert "max_extra_shops" not in tool.inputSchema["properties"]

    class Yandex:
        async def get_card(self, url, include_reviews, review_limit):
            return {"status": "ok", "product": {"title": url}, "error": None}

    s = MarketplaceService({"yandex_market": Yandex()})
    baseline = await s.get_cards(["a", "b", "a"])
    for limit in range(4):
        assert await s.get_cards(["a", "b", "a"], max_extra_shops=limit) == baseline
