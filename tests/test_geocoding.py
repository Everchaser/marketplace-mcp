"""Synthetic geocoder fixtures; explicit public-address consent only."""

import importlib.util
import json

import pytest

from marketplace_mcp.errors import MarketplaceError


@pytest.fixture
def geo():
    assert importlib.util.find_spec("marketplace_mcp.geocoding"), "public-address geocoder missing"
    from marketplace_mcp import geocoding

    return geocoding


CONFIG = {
    "address": "15 Панікахи, Дніпро, Україна",
    "geocoder": "nominatim",
    "public_address_consent": True,
    "geocoder_user_agent": "shopping-mcp/0.1 (public sample; contact: https://example.org/operator)",
}
RESULT = {
    "lat": "48.4",
    "lon": "35.0",
    "address": {"country_code": "ua", "city": "Дніпро", "road": "Панікахи", "house_number": "15"},
}


class Source:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def fetch(self, url):
        self.calls.append(url)
        return json.dumps(self.rows)


async def test_public_address_resolution_is_restricted_and_cached(geo, tmp_path):
    src = Source([RESULT])
    g = geo.PublicGeocoder(src, tmp_path)
    assert await g.resolve(CONFIG) == (48.4, 35.0)
    assert await geo.PublicGeocoder(src, tmp_path).resolve(CONFIG) == (48.4, 35.0)
    assert len(src.calls) == 1
    assert "countrycodes=ua" in src.calls[0] and "limit=2" in src.calls[0]
    assert geo.ATTRIBUTION == "© OpenStreetMap contributors (ODbL)"


@pytest.mark.parametrize(
    "rows,code",
    [
        ([RESULT, dict(RESULT, lon="36")], "geocoding_ambiguous"),
        ([], "geocoding_not_found"),
        ([dict(RESULT, address={"country_code": "ua", "city": "Дніпро"})], "geocoding_imprecise"),
        ([dict(RESULT, address={**RESULT["address"], "country_code": "ru"})], "geocoding_imprecise"),
    ],
)
async def test_ambiguous_or_city_only_results_fail_closed(geo, tmp_path, rows, code):
    with pytest.raises(MarketplaceError) as e:
        await geo.PublicGeocoder(Source(rows), tmp_path).resolve(CONFIG)
    assert e.value.code == code


async def test_disabled_or_unconsented_address_never_leaves_host(geo, tmp_path):
    src = Source([RESULT])
    with pytest.raises(MarketplaceError):
        await geo.PublicGeocoder(src, tmp_path).resolve({**CONFIG, "geocoder": "disabled"})
    assert not src.calls


async def test_generic_user_agent_requires_operator_identification(geo, tmp_path):
    src = Source([RESULT])
    with pytest.raises(MarketplaceError):
        await geo.PublicGeocoder(src, tmp_path).resolve({**CONFIG, "geocoder_user_agent": "python-httpx"})
    assert not src.calls


async def test_cross_instance_pacing_covers_internal_retries(geo, tmp_path):
    import asyncio
    import time

    starts = []

    class RetryingSource(Source):
        async def fetch(self, url):
            starts.append(time.monotonic())
            if len(starts) == 1:
                await asyncio.sleep(1.1)
                starts.append(time.monotonic())
            return json.dumps([RESULT])

    src = RetryingSource([RESULT])
    await geo.PublicGeocoder(src, tmp_path).resolve(CONFIG)
    await geo.PublicGeocoder(src, tmp_path).resolve({**CONFIG, "address": CONFIG["address"] + " public"})
    assert starts[-1] - starts[-2] >= 1.0, "Global rate gate ignored an internal retry"


async def test_public_geocoder_uses_cancellation_bound_channel(geo, tmp_path):
    class DirectSource(Source):
        async def fetch(self, url):
            raise AssertionError("Shielded transport cache must not outlive global rate gate")

        async def fetch_uncached(self, url):
            self.calls.append(url)
            return json.dumps([RESULT])

    src = DirectSource([RESULT])
    assert await geo.PublicGeocoder(src, tmp_path).resolve(CONFIG) == (48.4, 35.0)
    assert len(src.calls) == 1
