"""Shopping location settings; all addresses here are synthetic/public-test."""

import importlib.util

import pytest


@pytest.fixture
def settings():
    assert importlib.util.find_spec("marketplace_mcp.settings") is not None, "settings feature missing"
    from marketplace_mcp import settings

    return settings


from marketplace_mcp.errors import MarketplaceError


def test_default_path_and_missing_settings_are_safe(tmp_path, monkeypatch, settings):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert settings.settings_path() == tmp_path / "shopping/settings.json"
    assert settings.load_settings() == {}


def test_address_requires_explicit_public_geocoding_consent(settings):
    with pytest.raises(MarketplaceError, match="consent"):
        settings.location({"address": "public sample"})


def test_coordinates_and_override_do_not_geocode(settings):
    assert settings.location({"coordinates": {"lat": 48.4, "lon": 35.0}}) == (48.4, 35.0)
    assert settings.location({"shop_id": 1, "address": "private"}) is None


@pytest.mark.parametrize(
    "coords", [{"lat": True, "lon": 35}, {"lat": 91, "lon": 35}, {"lat": "nan", "lon": 35}]
)
def test_invalid_coordinates_fail_closed(coords, settings):
    with pytest.raises(MarketplaceError):
        settings.location({"coordinates": coords})
