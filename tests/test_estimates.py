"""Quantity estimation vertical slice, synthetic data only."""

import pytest

from marketplace_mcp.core import MarketplaceService


class Provider:
    async def get_card(self, url, include_reviews, review_limit):
        if url == "error":
            raise ValueError("private exception")
        state = "unknown" if url == "unknown" else "unavailable" if url == "absent" else "available"
        return {
            "status": "ok",
            "shop": {"id": 1, "tms_id": "1001"},
            "product": {
                "availability": {"state": state, "exact_quantity": None},
                "prices": {
                    "currency": "UAH",
                    "ordinary": None if url == "missing" else "0.10",
                    "promotional": "0.07",
                    "basis": "kg" if url == "weight" else None if url == "unclear" else "unit",
                },
            },
            "error": None,
        }


async def test_decimal_totals_keep_duplicates_and_partial_coverage():
    s = MarketplaceService({"varus": Provider()})
    try:
        r = await s.get_cards(
            ["ok", "ok", "absent", "unknown", "missing", "error", "unclear"],
            "varus",
            quantities=["3", "2", "1", "1", "1", "1", "1"],
        )
    except TypeError:
        pytest.fail("get_cards quantity contract missing")
    e = r["estimate"]
    assert e["currency"] == "UAH" and e["ordinary"]["total"] == "0.50"
    assert e["ordinary"]["included"] == 2 and e["ordinary"]["complete"] is False
    assert e["promotional"]["total"] == "0.42" and e["promotional"]["included"] == 3
    assert r["requested"] == 7 and r["returned"] == 6 and r["failed"] == 1
    assert e["exact_quantity_confirmed"] is False


async def test_kg_only_when_schema_says_kg():
    try:
        r = await MarketplaceService({"varus": Provider()}).get_cards(
            ["weight", "ok"], "varus", quantities=["0.5", "1"], units=["kg", "kg"]
        )
    except TypeError:
        pytest.fail("get_cards weight contract missing")
    assert r["estimate"]["ordinary"]["total"] == "0.05"
    assert r["estimate"]["ordinary"]["included"] == 1
    assert r["estimate"]["lines"][1]["excluded_reason"] == "unit_mismatch"


@pytest.mark.parametrize("quantities", [["NaN"], ["0"], ["-1"], ["1e999999"], ["0.5"], ["1", "2"]])
async def test_quantity_validation_is_explicit(quantities):
    try:
        r = await MarketplaceService({"varus": Provider()}).get_cards(["ok"], "varus", quantities=quantities)
    except TypeError:
        pytest.fail("get_cards quantity validation missing")
    assert r["status"] == "error" and r["error"]["code"] == "validation_error"


async def test_malformed_unit_returns_validation_error_not_exception():
    r = await MarketplaceService({"varus": Provider()}).get_cards(
        ["ok"], "varus", quantities=["1"], units=[["kg"]]
    )
    assert r["status"] == "error" and r["error"]["code"] == "validation_error"
