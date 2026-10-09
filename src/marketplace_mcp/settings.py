"""Private location stays local unless public-address geocoding is explicitly enabled."""

import json
import math
import os
from pathlib import Path

from .errors import MarketplaceError


def settings_path(override=None):
    return (
        Path(override).expanduser()
        if override
        else Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "shopping/settings.json"
    )


def load_settings(override=None):
    path = settings_path(override)
    if not path.exists():
        return {}
    try:
        if path.stat().st_size > 65536:
            raise ValueError
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or not isinstance(data.get("varus", {}), dict):
            raise TypeError
        return data.get("varus", {})
    except (ValueError, TypeError, OSError):
        return {"configuration_error": True}


def coordinates(lat, lon):
    try:
        if isinstance(lat, bool) or isinstance(lon, bool):
            raise TypeError
        lat, lon = float(lat), float(lon)
        if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError
        return lat, lon
    except (ValueError, TypeError):
        raise MarketplaceError("invalid_location", "Invalid location coordinates") from None


def location(config):
    if config.get("configuration_error"):
        raise MarketplaceError("invalid_settings", "Shopping settings are invalid or unreadable")
    if config.get("shop_id") is not None:
        if type(config["shop_id"]) is not int or config["shop_id"] <= 0:
            raise MarketplaceError("invalid_location", "shop_id must be a positive physical shop id")
        return None
    if "coordinates" in config:
        c = config["coordinates"]
        if not isinstance(c, dict):
            raise MarketplaceError("invalid_location", "Invalid location coordinates")
        return coordinates(c.get("lat"), c.get("lon"))
    if config.get("address"):
        if config.get("geocoder") != "nominatim" or config.get("public_address_consent") is not True:
            raise MarketplaceError(
                "geocoding_consent_required",
                "Public non-confidential address geocoding consent required; prefer coordinates or shop_id",
            )
        return None
    raise MarketplaceError(
        "location_unconfigured", "Configure VARUS coordinates or physical shop_id in shopping settings"
    )
