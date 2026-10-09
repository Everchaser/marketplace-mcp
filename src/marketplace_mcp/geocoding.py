"""Opt-in public-address geocoding only. No generic geocoding MCP tool."""

import asyncio
import fcntl
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlencode

from .errors import MarketplaceError
from .settings import coordinates, location
from .varus_transport import VarusTransport

ATTRIBUTION = "© OpenStreetMap contributors (ODbL)"
POLICY = "https://operations.osmfoundation.org/policies/nominatim/"
ENDPOINT = "https://nominatim.openstreetmap.org/search"


class PublicGeocoder:
    def __init__(self, transport=None, cache_dir=None):
        self.transport = transport
        self.cache_dir = (
            Path(cache_dir)
            if cache_dir
            else Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "shopping/geocoding"
        )
        self._lock = asyncio.Lock()

    async def resolve(self, config):
        location(config)  # enforces disabled/consent before any network or persistence
        if config.get("geocoder") != "nominatim" or config.get("public_address_consent") is not True:
            raise MarketplaceError("geocoding_consent_required", "Public address consent required")
        ua = config.get("geocoder_user_agent", "")
        address = config.get("address")
        if not isinstance(address, str) or not 5 <= len(address) <= 300:
            raise MarketplaceError("invalid_location", "Public address must be 5..300 characters")
        if (
            not isinstance(ua, str)
            or not ua.startswith("shopping-mcp/")
            or not ("https://" in ua or "@" in ua)
            or len(ua) > 300
            or any(ord(c) < 32 for c in ua)
        ):
            raise MarketplaceError(
                "geocoding_identification_required",
                "Set an identifiable shopping-mcp User-Agent with operator contact; use coordinates otherwise",
            )
        if config.get("geocoder_endpoint", ENDPOINT) != ENDPOINT:
            raise MarketplaceError(
                "geocoding_endpoint_denied",
                "Only the public Nominatim HTTPS endpoint is supported; disable it or use coordinates to switch service",
            )
        key = hashlib.sha256((ENDPOINT + "\0" + address).encode()).hexdigest()
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            async with self._lock, asyncio.timeout(90):
                with (self.cache_dir / "request.lock").open("a+") as gate:
                    while True:
                        try:
                            fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            break
                        except BlockingIOError:
                            await asyncio.sleep(0.1)
                    with sqlite3.connect(self.cache_dir / "results.sqlite") as db:
                        db.execute(
                            "CREATE TABLE IF NOT EXISTS results (key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
                        )
                        row = db.execute("SELECT payload FROM results WHERE key=?", (key,)).fetchone()
                        if row:
                            return self._decode(json.loads(row[0]))
                        gate.seek(0)
                        try:
                            last = float(gate.read() or "0")
                        except ValueError:
                            last = time.time()
                        await asyncio.sleep(max(0, 1.1 - (time.time() - last)))
                        gate.seek(0)
                        gate.truncate()
                        gate.write(str(time.time()))
                        gate.flush()
                        transport = self.transport or VarusTransport(
                            host="nominatim.openstreetmap.org",
                            user_agent=ua,
                            max_bytes=256 * 1024,
                            cache_ttl=86400,
                        )
                        url = (
                            ENDPOINT
                            + "?"
                            + urlencode(
                                {
                                    "q": address,
                                    "countrycodes": "ua",
                                    "format": "jsonv2",
                                    "addressdetails": 1,
                                    "limit": 2,
                                }
                            )
                        )
                        try:
                            try:
                                fetch = getattr(transport, "fetch_uncached", transport.fetch)
                                rows = json.loads(await fetch(url))
                            finally:
                                # Keep subsequent processes paced after the last internal retry.
                                gate.seek(0)
                                gate.truncate()
                                gate.write(str(time.time()))
                                gate.flush()
                            result = self._result(rows)
                        except (ValueError, TypeError, KeyError):
                            raise MarketplaceError(
                                "geocoding_extraction_failed", "Geocoder response could not be verified"
                            ) from None
                        db.execute("INSERT OR REPLACE INTO results VALUES (?,?)", (key, json.dumps(result)))
                        db.commit()
                        return self._decode(result)
        except (OSError, sqlite3.Error, TimeoutError):
            raise MarketplaceError(
                "geocoding_unavailable", "Geocoding cache or request unavailable; use saved coordinates"
            ) from None

    @staticmethod
    def _result(rows):
        if not isinstance(rows, list):
            raise TypeError
        if not rows:
            return {"error": "geocoding_not_found"}
        if len(rows) != 1:
            return {"error": "geocoding_ambiguous"}
        row = rows[0]
        a = row.get("address", {})
        if (
            a.get("country_code") != "ua"
            or not a.get("house_number")
            or not a.get("road")
            or not any(a.get(k) for k in ("city", "town", "village"))
        ):
            return {"error": "geocoding_imprecise"}
        try:
            lat, lon = coordinates(row.get("lat"), row.get("lon"))
        except MarketplaceError:
            return {"error": "geocoding_imprecise"}
        return {"coordinates": [lat, lon]}

    @staticmethod
    def _decode(result):
        if result.get("error"):
            raise MarketplaceError(
                result["error"],
                "Public address could not be uniquely resolved to a precise Ukrainian location; save verified coordinates instead",
            )
        return coordinates(*result["coordinates"])
