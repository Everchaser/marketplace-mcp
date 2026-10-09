"""VARUS catalogue/SSR adapter with explicit physical-shop semantics."""

import asyncio
import copy
import json
import math
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from urllib.parse import urlencode, urlsplit

from .errors import MarketplaceError
from .estimates import VerifiedBatchAllocations
from .geocoding import ATTRIBUTION, POLICY
from .settings import coordinates, location
from .varus_transport import validate_url

BASE = "https://varus.ua"
CATALOG = BASE + "/api/catalog/vue_storefront_catalog_2/"


def query_url(entity, filters, size, start=0, fields=None):
    request = {"_availableFilters": [], "_appliedFilters": filters, "_appliedSort": [], "_searchText": ""}
    params = {
        "from": start,
        "request": json.dumps(request, separators=(",", ":")),
        "request_format": "search-query",
        "response_format": "compact",
        "size": size,
        "sort": "",
    }
    if fields:
        params["_source_include"] = fields
    return CATALOG + entity + "/_search?" + urlencode(params)


def filter_value(attribute, value):
    return {"attribute": attribute, "value": value, "scope": "default"}


SHOPS_URL = query_url("tms_shop", [filter_value("status", {"eq": "1"})], 250)


class Canonicals(HTMLParser):
    # Treat RCDATA as raw text too: neither can contain executable markup.
    TEXT_CONTAINERS = frozenset(
        {"script", "style", "title", "textarea", "xmp", "iframe", "noembed", "noframes", "noscript"}
    )

    def __init__(self):
        super().__init__()
        self.urls = []
        self.scripts = []
        self.script = None
        self.template_depth = 0
        self.plaintext = False

    def handle_starttag(self, tag, attrs):
        if self.plaintext:
            return
        if tag == "plaintext":
            # Unlike raw text, plaintext has no closing tag in HTML documents.
            self.plaintext = True
            return
        if tag == "template":
            self.template_depth += 1
        if tag in self.TEXT_CONTAINERS:
            # Explicit modes also cover older Python 3.12 HTMLParser versions.
            # Live SSR assumes scripting enabled, so noscript is raw text.
            self.set_cdata_mode(tag)
        if self.template_depth:
            return
        a = dict(attrs)
        if tag == "link" and a.get("rel") == "canonical" and a.get("href"):
            self.urls.append(a["href"])
        if (
            tag == "script"
            and "src" not in a
            and a.get("type", "").lower()
            in {
                "",
                "text/javascript",
                "application/javascript",
            }
        ):
            self.script = []

    def handle_startendtag(self, tag, attrs):
        # HTML ignores the self-closing flag on these non-void elements.
        # HTMLParser otherwise synthesizes an end tag and exits inert context.
        if tag in self.TEXT_CONTAINERS or tag in {"template", "plaintext"}:
            self.handle_starttag(tag, attrs)
        else:
            super().handle_startendtag(tag, attrs)

    def handle_data(self, data):
        if self.script is not None and not self.plaintext:
            self.script.append(data)

    def handle_endtag(self, tag):
        if self.plaintext:
            return
        if tag == "template" and self.template_depth:
            self.template_depth -= 1
        if tag == "script" and self.script is not None:
            self.scripts.append("".join(self.script))
            self.script = None


def parse_card(html, url):
    try:
        parser = Canonicals()
        parser.feed(html)
        parser.close()
        scripts = [s for s in parser.scripts if "__INITIAL_STATE__" in s]
        if len(scripts) != 1:
            raise ValueError
        script = scripts[0].strip()
        marker = re.match(r"window\.__INITIAL_STATE__\s*=\s*", script)
        if not marker:
            raise ValueError
        state, end = json.JSONDecoder().raw_decode(script, marker.end())
        # The only supported live trailer removes the SSR script element.
        # It is matched as text, never executed; arbitrary JS fails closed.
        trailer = (
            ";(function(){var s;(s=document.currentScript||"
            "document.scripts[document.scripts.length-1]).parentNode.removeChild(s);}());"
        )
        if script[end:].strip() not in {";", trailer}:
            raise ValueError
        product = state["product"]["current"]
        if not isinstance(product, dict):
            raise TypeError
        for field in ("id", "sku"):
            value = product[field]
            if type(value) is int:
                identity = value
            elif isinstance(value, str) and re.fullmatch(r"[0-9]{1,19}", value):
                identity = int(value)
            else:
                raise ValueError
            if not 0 < identity <= 2**63 - 1:
                raise ValueError
        for field, bound in (("name", 1000), ("url_key", 251)):
            value = product[field]
            if not isinstance(value, str) or not value.strip() or len(value) > bound:
                raise ValueError
        canonical = list(dict.fromkeys(parser.urls))
        if len(canonical) != 1:
            raise ValueError
        validate_url(canonical[0])
        if urlsplit(canonical[0]).path != urlsplit(url).path:
            raise ValueError
        if product.get("url_key") != urlsplit(canonical[0]).path.lstrip("/"):
            raise ValueError
        return product, canonical[0]
    except (ValueError, KeyError, TypeError, RecursionError):
        raise MarketplaceError(
            "extraction_failed", "VARUS product identity/SSR could not be verified"
        ) from None


def money(value):
    try:
        if value is None or isinstance(value, bool):
            return None
        d = Decimal(str(value))
        return str(d) if d.is_finite() and d >= 0 else None
    except (ValueError, InvalidOperation):
        return None


def eligible(shop):
    return (
        str(shop.get("status")) == "1"
        and str(shop.get("is_enabled")) == "1"
        and not shop.get("inactive_reason")
        and str(shop.get("is_darkstore")) == "0"
        and str(shop.get("is_darkhub")) == "0"
        and shop.get("is_shipping_hub") in (False, 0, "0")
    )


def distance(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(min(1, max(0, h))))


class VarusProvider:
    def __init__(self, transport, config, geocoder=None):
        self.transport = transport
        self.config = config
        self.geocoder = geocoder

    @staticmethod
    def validate_card_url(url):
        validate_url(url)
        if not re.fullmatch(r"/[a-z0-9][a-z0-9_-]{1,250}", urlsplit(url).path) or urlsplit(url).path in {
            "/stores",
            "/search",
        }:
            raise ValueError("Expected a VARUS product URL")

    async def selected_shop(self, with_alternatives=False):
        point = location(self.config)
        if with_alternatives and self.config.get("shop_id") is not None and "coordinates" in self.config:
            # Single-card override behavior stays unchanged. Batch alternatives use
            # the original explicit coordinate anchor, not the override shop.
            point = location({k: v for k, v in self.config.items() if k != "shop_id"})
        if point is None and self.config.get("shop_id") is None:
            if self.geocoder is None:
                raise MarketplaceError(
                    "geocoding_unavailable", "Public-address geocoder is unavailable; use coordinates"
                )
            point = await self.geocoder.resolve(self.config)
        try:
            data = json.loads(await self.transport.fetch(SHOPS_URL))
            shops = data["hits"]
            if not isinstance(shops, list) or data["total"] != {"value": len(shops), "relation": "eq"}:
                raise ValueError
            candidates = []
            for s in shops:
                if not isinstance(s, dict) or not eligible(s) or not s.get("id") or not s.get("tms_id"):
                    continue
                try:
                    c = coordinates(s.get("lat"), s.get("long"))
                except MarketplaceError:
                    continue
                candidates.append((distance(point, c) if point else 0, str(s["id"]), s, c))
            primary_candidates = [
                row
                for row in candidates
                if self.config.get("shop_id") is None or row[2]["id"] == self.config["shop_id"]
            ]
            if not primary_candidates:
                raise MarketplaceError(
                    "shop_unavailable", "No eligible operating physical shop matched location"
                )
            dist, _, s, c = min(primary_candidates, key=lambda x: (x[0], x[1]))
            for field in ("id", "tms_id"):
                if sum(str(row.get(field)) == str(s[field]) for row in shops if isinstance(row, dict)) != 1:
                    raise ValueError
            primary = {
                "id": s["id"],
                "tms_id": str(s["tms_id"]),
                "address": s.get("address"),
                "coordinates": {"lat": c[0], "lon": c[1]},
                "selection": "shop_id_override" if self.config.get("shop_id") else "nearest_straight_line",
                "distance_km": round(dist, 3) if point else None,
                "source": SHOPS_URL,
                **(
                    {
                        "geocoding": {
                            "attribution": ATTRIBUTION,
                            "policy": POLICY,
                            "warning": "Explicit public non-confidential address consent; no private/home addresses allowed",
                        }
                    }
                    if self.config.get("shop_id") is None and "coordinates" not in self.config
                    else {}
                ),
            }
            if not with_alternatives:
                return primary
            origin = point or c
            alternatives = []
            for _, _, row, coords in candidates:
                if row["id"] == s["id"]:
                    continue
                if any(
                    sum(
                        str(other.get(field)) == str(row[field]) for other in shops if isinstance(other, dict)
                    )
                    != 1
                    for field in ("id", "tms_id")
                ):
                    continue
                alternatives.append(
                    {
                        "id": row["id"],
                        "tms_id": str(row["tms_id"]),
                        "address": row.get("address"),
                        "coordinates": {"lat": coords[0], "lon": coords[1]},
                        "selection": "nearest_missing_item_fallback",
                        "distance_km": round(distance(origin, coords), 3),
                        "source": SHOPS_URL,
                    }
                )
            # Rank by unrounded distance; never hop the origin to the latest shop.
            alternatives.sort(
                key=lambda row: (
                    distance(origin, (row["coordinates"]["lat"], row["coordinates"]["lon"])),
                    str(row["id"]),
                )
            )
            if point is None:
                primary = primary | {"distance_km": 0.0}
            return (
                primary,
                alternatives,
                {
                    "origin": {"lat": origin[0], "lon": origin[1]},
                    "origin_source": "primary_shop_coordinates"
                    if point is None
                    else "configured_coordinates"
                    if "coordinates" in self.config
                    else "resolved_public_address",
                },
            )
        except (ValueError, KeyError, TypeError):
            raise MarketplaceError(
                "shop_extraction_failed", "VARUS shop catalogue could not be verified"
            ) from None

    async def search(self, query, limit, page):
        shop = await self.selected_shop()
        url = query_url(
            "product_v2",
            [
                *(filter_value("name", {"like": term}) for term in query.casefold().split()),
                filter_value("status", {"eq": 1}),
                filter_value("visibility", {"eq": 4}),
            ],
            limit,
            (page - 1) * limit,
            "id,sku,name,url_key,url_path,slug",
        )
        try:
            data = json.loads(await self.transport.fetch(url))
            hits = data["hits"]
            total = data["total"]
            if (
                not isinstance(hits, list)
                or len(hits) > limit
                or total.get("relation") != "eq"
                or type(total.get("value")) is not int
            ):
                raise ValueError
        except (ValueError, KeyError, TypeError, AttributeError):
            raise MarketplaceError("extraction_failed", "VARUS catalogue search schema invalid") from None
        items, warnings, candidate_errors = [], [], []
        for index, hit in enumerate(hits):
            try:
                candidate = BASE + "/" + hit["url_key"]
                self.validate_card_url(candidate)
                p, canonical = parse_card(await self.transport.fetch(candidate), candidate)
                if str(p.get("id")) != str(hit.get("id")) or str(p.get("sku")) != str(hit.get("sku")):
                    raise ValueError
                items.append(
                    {
                        "url": canonical,
                        "title": p["name"],
                        "id": p["id"],
                        "sku": str(p["sku"]),
                        "currency": "UAH",
                        "url_provenance": "observed_card_canonical_and_ssr_identity",
                    }
                )
            except (ValueError, KeyError, TypeError, MarketplaceError) as exc:
                candidate_errors.append(
                    {
                        "index": index,
                        "error": exc.as_dict()
                        if isinstance(exc, MarketplaceError)
                        else {
                            "code": "identity_unverified",
                            "message": "Catalogue/card identity mismatch",
                            "retryable": False,
                        },
                    }
                )
                warnings.append("A catalogue candidate could not be verified; not evidence of unavailability")
        return {
            "status": "partial" if warnings else "ok",
            "candidate_errors": candidate_errors,
            "items": items,
            "returned": len(items),
            "total_available": total["value"],
            "has_next_page": page * limit < total["value"],
            "shop": shop,
            "warnings": warnings
            + [
                "Token-AND catalogue name search, not the website external relevance search; search prices/stock require get_card"
            ],
            "extraction": {
                "source": url,
                "method": "catalogue name filter + observed canonical SSR identity",
            },
            "error": None,
        }

    async def get_cards(self, urls, include_reviews=True, review_limit=10, max_extra_shops=3):
        results = []
        valid_indices = []
        for i, url in enumerate(urls):
            line = {
                "marketplace": "varus",
                "status": "error",
                "url": url,
                "product": None,
                "warnings": [],
                "error": None,
            }
            try:
                self.validate_card_url(url)
                valid_indices.append(i)
            except ValueError as exc:
                line["error"] = {"code": "validation_error", "message": str(exc), "retryable": False}
            results.append(line)
        shop, alternatives, anchor = None, [], {"origin": None, "origin_source": None}
        if valid_indices:
            try:
                shop, alternatives, anchor = await self.selected_shop(with_alternatives=True)
            except Exception as exc:  # noqa: BLE001 - preserve URL errors on catalogue failure
                failure = (
                    exc.as_dict()
                    if isinstance(exc, MarketplaceError)
                    else {
                        "code": "provider_error",
                        "message": "Provider failed unexpectedly",
                        "retryable": False,
                    }
                )
                for i in valid_indices:
                    results[i]["error"] = failure
                valid_indices = []
        gate = asyncio.Semaphore(2)
        cards = {}

        async def one(url):
            result = {
                "marketplace": "varus",
                "status": "error",
                "url": url,
                "product": None,
                "warnings": [],
                "error": None,
            }
            try:
                self.validate_card_url(url)
                async with gate:
                    p, canonical, document = await self._load_card(url)
                cards[url] = (p, canonical, document)
                result.update(self._render_card(url, shop, p, canonical, document))
            except ValueError as exc:
                result["error"] = {"code": "validation_error", "message": str(exc), "retryable": False}
            except MarketplaceError as exc:
                result["error"] = exc.as_dict()
            except Exception:  # noqa: BLE001 - isolate individual card failures
                result["error"] = {
                    "code": "provider_error",
                    "message": "Provider failed unexpectedly",
                    "retryable": False,
                }
            return result

        # Input URL strings remain exact; no SKU/URL normalization or substitutions.
        unique = list(dict.fromkeys(urls[i] for i in valid_indices))
        loaded = dict(zip(unique, await asyncio.gather(*(one(url) for url in unique)), strict=True))
        for i in valid_indices:
            results[i] = copy.deepcopy(loaded[urls[i]])
        attempted = []
        for candidate in [shop, *alternatives[:max_extra_shops]]:
            indices = [
                i
                for i, r in enumerate(results)
                if r["status"] != "error"
                and (candidate == shop or r["product"]["availability"]["state"] != "available")
            ]
            if not indices:
                break
            attempted.append(candidate | {"checked_indices": indices})
            for i in indices:
                line = results[i]
                observation = self._render_card(urls[i], candidate, *cards[urls[i]])
                if candidate == shop:
                    line["primary_observation"] = {"shop": shop, "product": copy.deepcopy(line["product"])}
                    line["shop_attempts"] = []
                line["shop_attempts"].append(
                    {
                        "shop": candidate,
                        "availability": observation["product"]["availability"],
                        "prices": observation["product"]["prices"],
                        "observation": observation["product"]["observation"],
                    }
                )
                if observation["product"]["availability"]["state"] == "available":
                    line.update(observation)
        unresolved = [
            {
                "index": i,
                "reason": r["error"]["code"]
                if r["status"] == "error"
                else r["product"]["availability"]["state"],
            }
            for i, r in enumerate(results)
            if r["status"] == "error" or r["product"]["availability"]["state"] != "available"
        ]
        groups = []
        for candidate in attempted:
            indices = [
                i
                for i, r in enumerate(results)
                if r["status"] != "error"
                and r["product"]["availability"]["state"] == "available"
                and (r["shop"]["id"], r["shop"]["tms_id"]) == (candidate["id"], candidate["tms_id"])
            ]
            if indices:
                groups.append(
                    {
                        "shop": results[indices[0]]["shop"],
                        "indices": indices,
                        "count": len(indices),
                        "items": [
                            {
                                "index": i,
                                "url": urls[i],
                                "sku": results[i]["product"]["sku"],
                                "prices": results[i]["product"]["prices"],
                            }
                            for i in indices
                        ],
                    }
                )
        return {
            "results": results,
            "allocations": groups,
            "_estimate_allocations": VerifiedBatchAllocations.from_groups(results, groups),
            "primary_shop": shop,
            "attempted_shops": attempted,
            "available": len(results) - len(unresolved),
            "unresolved": unresolved,
            "fallback": {
                **anchor,
                "max_extra_shops": max_extra_shops,
                "extra_shops_inspected": max(0, len(attempted) - 1),
                "stop_reason": "no_valid_cards"
                if not attempted
                else "all_valid_lines_available"
                if not any(
                    r["status"] != "error" and r["product"]["availability"]["state"] != "available"
                    for r in results
                )
                else "catalogue_exhausted"
                if len(attempted) == len(alternatives) + 1
                else "extra_shop_limit",
            },
        }

    async def get_card(self, url, include_reviews=True, review_limit=10):
        self.validate_card_url(url)
        shop = await self.selected_shop()
        p, canonical, document = await self._load_card(url)
        return self._render_card(url, shop, p, canonical, document)

    async def _load_card(self, url):
        document = (
            await self.transport.fetch_document(url)
            if hasattr(self.transport, "fetch_document")
            else {"text": await self.transport.fetch(url), "fetched_at": datetime.now(UTC).isoformat()}
        )
        p, canonical = parse_card(document["text"], url)
        if not isinstance(p.get("shop_availability_list", []), list):
            raise MarketplaceError("extraction_failed", "VARUS shop availability schema invalid")
        return p, canonical, document

    @staticmethod
    def _render_card(url, shop, p, canonical, document):
        entries = p.get("shop_availability_list", [])
        if not isinstance(entries, list):
            raise MarketplaceError("extraction_failed", "VARUS shop availability schema invalid")
        matched = [s for s in entries if isinstance(s, dict) and str(s.get("tms_id")) == shop["tms_id"]]
        entry = matched[0] if len(matched) == 1 else {}
        if entry.get("is_enabled") in (False, 0, "0") or entry.get("inactive_reason"):
            entry = {}
        stock = entry.get("product_in_stock")
        state = "available" if stock is True else "unavailable" if stock is False else "unknown"
        now = datetime.now(UTC).isoformat()
        promotional = money(entry.get("special_price"))
        # Live kg SSR displays special='' for special_price=0/discount_percent=0.
        # VARUS zero special price is a no-promotion sentinel, not a free offer.
        if promotional is not None and Decimal(promotional) == 0:
            promotional = None
        prices = {
            "currency": "UAH",
            "ordinary": money(entry.get("price")),
            "promotional": promotional,
            "basis": {"pc": "unit", "kg": "kg"}.get(p.get("unit")),
        }
        return {
            "status": "ok",
            "shop": shop,
            "product": {
                "url": url,
                "source_url": canonical,
                "id": p.get("id"),
                "sku": str(p["sku"]),
                "title": p["name"],
                "currency": "UAH",
                "prices": prices,
                "availability": {
                    "state": state,
                    "exact_quantity": None,
                    "source_shop_id": entry.get("id"),
                    "tms_id": shop["tms_id"],
                    "source": url,
                    "observed_at": document["fetched_at"],
                },
                "observation": {k: v for k, v in document.items() if k != "text"} | {"parsed_at": now},
                "reviews": [],
                "reviews_returned": 0,
                "delivery": None,
            },
            "warnings": [
                "Exact requested quantity and delivery fulfillment are unknown",
                "Upstream/cache observation may be stale and is not a reservation",
                "VARUS reviews are not extracted",
            ],
            "error": None,
        }
