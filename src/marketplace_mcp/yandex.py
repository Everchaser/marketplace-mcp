"""Anonymous Yandex SSR adapter; discount eligibility remains unverified."""

import json
from collections import Counter
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urlsplit

from .core import error
from .errors import MarketplaceError
from .urls import BASE, validate_url
from .vendor import yandex_ssr as ssr

CONDITIONAL_LABEL = "unverified discounted price; eligibility/payment conditions unknown"


def parse(function, html):
    try:
        return function(html)
    except (TypeError, ValueError, KeyError, AttributeError, OverflowError):
        raise MarketplaceError("parser_drift", "Yandex SSR structure could not be parsed") from None


def product(raw, url, include_reviews=True, review_limit=10):
    reviews = raw.get("reviews", [])[:review_limit] if include_reviews else []
    result = {
        key: raw.get(key) or None
        for key in (
            "sku_id",
            "title",
            "description",
            "brand",
            "seller",
            "price_rub",
            "rating",
            "rating_count",
            "review_count",
            "image",
        )
    }
    result.update(
        id=str(raw["product_id"]) if raw.get("product_id") else None,
        url=url,
        sku_id=str(raw["sku_id"]) if raw.get("sku_id") else None,
        price_conditional_rub=raw.get("price_with_plus"),
        conditional_price_label=CONDITIONAL_LABEL if raw.get("price_with_plus") is not None else None,
        currency="RUB",
        price_old_rub=raw.get("price_old_rub", raw.get("price_before_discount_rub")),
        reviews=reviews,
        reviews_returned=len(reviews),
        delivery=None,
        delivery_status="unknown",
    )
    return result


def _zone_identity(payload):
    return (
        str(payload.get("oskuId") or payload.get("modelId") or ""),
        str(payload.get("marketSku") or ""),
        str(payload.get("wareId") or ""),
    )


def _annotate_zone_identities(html, rows):
    # Use the vendor row extractor and its exact full-offer deduplication, not
    # HTMLParser's broader accepted attribute syntax or independent scope order.
    identities = list(dict.fromkeys(_zone_identity(p) for p in ssr.zone_snippets(html)))
    if len(identities) != len(rows):
        return
    for row, identity in zip(rows, identities, strict=True):
        if (str(row.get("product_id") or ""), str(row.get("sku_id") or "")) == identity[:2]:
            row["_zone_identity"] = identity


class _SourceLinks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.by_id = {}
        self.by_sku = {}
        self.observed = set()
        self.explicit_by_sku = {}
        self.scoped_explicit = {}
        self.scoped = {}
        self.stack = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        identity = self.stack[-1][1] if self.stack else None
        if attrs.get("data-zone-name") == "productSnippet":
            try:
                payload = json.loads(attrs.get("data-zone-data", ""))
            except ValueError:
                payload = None
            if isinstance(payload, dict) and payload.get("type") == "offer":
                identity = _zone_identity(payload)
                self.add_explicit(payload.get("url"), *identity[:2], scope=identity)
        if tag not in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }:
            self.stack.append((tag, identity))
        if tag != "a":
            return
        href = attrs.get("href")
        if not href:
            return
        url = BASE + href if href.startswith("/") and not href.startswith("//") else href
        try:
            validate_url(url)
        except ValueError:
            return
        self.observed.add(url)
        if identity and self.matches(url, *identity[:2]):
            self.scoped.setdefault(identity, set()).add(url)
        if identity is None:
            identifier = urlsplit(url).path.rsplit("/", 1)[1]
            skus = parse_qs(urlsplit(url).query).get("sku", [])
            if not skus:
                self.by_id.setdefault(identifier, set()).add(url)
            for sku in skus:
                self.by_sku.setdefault((identifier, sku), set()).add(url)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def add_explicit(self, url, identifier, sku, *, scope=None):
        if isinstance(url, str) and url.startswith("/") and not url.startswith("//"):
            url = BASE + url
        try:
            validate_url(url)
        except ValueError:
            return
        if not self.matches(url, identifier, sku):
            return
        self.observed.add(url)
        target = self.scoped_explicit if scope is not None else self.explicit_by_sku
        target.setdefault(scope if scope is not None else (identifier, sku), set()).add(url)

    @staticmethod
    def matches(url, identifier, sku):
        parts = urlsplit(url)
        skus = parse_qs(parts.query).get("sku", [])
        return parts.path.rsplit("/", 1)[-1] in {identifier, sku} and (not sku or not skus or skus == [sku])


class YandexMarketProvider:
    def __init__(self, transport):
        self.transport = transport

    @staticmethod
    def validate_card_url(url):
        return validate_url(url)

    async def get_card(self, url, include_reviews=True, review_limit=10):
        self.validate_card_url(url)
        html = await self.transport.fetch(url)
        raw = parse(ssr.parse_card, html)
        code = raw.get("status")
        if code not in {"captcha", "empty_product_shell"}:
            code = (
                "parser_drift"
                if not (raw.get("title") or raw.get("price_with_plus") or raw.get("price_rub"))
                else None
            )
        if code:
            return {
                "status": "error",
                "url": url,
                "product": None,
                "warnings": [],
                "error": error(
                    code, "Yandex page did not contain usable product data", code == "empty_product_shell"
                ),
            }
        # Upstream parse_card promotes the main discounted price when regular is absent.
        # Read the explicit regular row instead; never infer discount eligibility.
        merged = ssr.merge_card_collections(html)
        prices = ssr._first_dict(merged.get("price"))
        original_reviews = [
            r for r in ssr._as_list(ssr._as_dict(merged.get("reviews")).get("reviews")) if isinstance(r, dict)
        ]
        for review, original in zip(raw.get("reviews", []), original_reviews, strict=False):
            for key in ("author", "date", "pros", "cons", "comment"):
                review[key] = review.get(key) or None
            votes = ssr._as_dict(original.get("votes"))
            for key, source_key in (("votes_up", "votesAgree"), ("votes_down", "votesReject")):
                value = votes.get(source_key)
                review[key] = 0 if type(value) is int and value == 0 else ssr._to_int(value)
        raw["price_rub"] = next(
            (
                ssr._to_number(ssr._as_dict(p.get("price")).get("value"))
                for p in prices.get("oldPrices", [])
                if isinstance(p, dict) and p.get("type") == "regular"
            ),
            None,
        )
        return {
            "status": "ok",
            "url": url,
            "product": product(raw, url, include_reviews, review_limit),
            "warnings": [
                "SSR review sample may aggregate product variants; it is not the review total",
                CONDITIONAL_LABEL,
            ],
            "error": None,
        }

    async def search(self, query, limit=10, page=1):
        params = {"text": query, "page": page}
        html = await self.transport.fetch(f"{BASE}/search?{urlencode(params)}")
        raw = parse(ssr.parse_search, html)
        if raw.get("status") == "ok_zone":
            _annotate_zone_identities(html, raw.get("items", []))
        links = _SourceLinks()
        links.feed(html)
        collections = ssr.find_search_collections(html)
        for item in ssr.ldjson_item_list(html):
            parts = urlsplit(item["url"])
            identifier = parts.path.rsplit("/", 1)[-1]
            skus = parse_qs(parts.query).get("sku", [])
            links.add_explicit(
                item["url"], identifier, str(item.get("sku_id") or (skus[0] if len(skus) == 1 else ""))
            )
        for place in ssr._as_dict((collections or {}).get("productShowPlace")).values():
            place = ssr._as_dict(place)
            direct = ssr._as_dict(ssr._as_dict(place).get("urls")).get("direct")
            offer_place = ssr._as_dict(
                ssr._as_dict((collections or {}).get("offerShowPlace")).get(
                    place.get("defaultOfferShowPlaceId")
                )
            )
            offer = ssr._as_dict(
                ssr._as_dict((collections or {}).get("offer")).get(offer_place.get("offerId"))
            )
            snippet_id = place.get("productSnippetId")
            if not offer and isinstance(snippet_id, str) and snippet_id.startswith("prime-"):
                offer = ssr._as_dict(ssr._as_dict((collections or {}).get("offer")).get(snippet_id[6:]))
            direct = direct or offer.get("url")
            if direct:
                links.add_explicit(
                    ssr._absolute_url(direct), str(place.get("productId")), str(offer.get("skuId") or "")
                )
        visible = ssr._first_dict(
            (collections if collections is not None else ssr.merge_card_collections(html)).get(
                "visibleSearchResult"
            )
        )
        next_flag = visible.get("hasNextPage")
        status = raw.get("status")
        result = {
            "status": "ok",
            "query": query,
            "items": [],
            "returned": 0,
            "page": page,
            "has_next_page": next_flag if type(next_flag) is bool else None,
            "total_available": raw.get("total"),
            "warnings": [],
            "extraction": None,
            "error": None,
        }
        unusable_values = raw.get("items") and all(
            not (r.get("title") or r.get("price_rub") or r.get("price_with_plus")) for r in raw["items"]
        )
        if status in {"captcha", "no_products_found"} or unusable_values:
            code = "captcha" if status == "captcha" else "parser_drift"
            result.update(status="error", error=error(code, "Yandex search did not contain usable data"))
            return result
        result["extraction"] = {
            "ok": "ssr",
            "ok_zone": "zone",
            "ok_ldjson_only": "ld+json",
            "empty": "ssr",
        }.get(status)
        if status in {"ok_zone", "ok_ldjson_only"}:
            result["warnings"].append(
                "Degraded first-screen extraction; pagination and result total may be unknown"
            )
        seen = set()
        skipped_urls = 0
        product_rows = Counter(str(row.get("product_id")) for row in raw.get("items", []))
        for row in raw.get("items", []):
            url = row.get("url")
            if row.get("source") != "ld+json":
                identifier = str(row.get("product_id"))
                identity = (identifier, str(row.get("sku_id")))
                zone_identity = row.get("_zone_identity") if row.get("source") == "zone" else (*identity, "")
                candidates = (
                    links.scoped_explicit.get(zone_identity)
                    or links.scoped.get(zone_identity)
                    or links.explicit_by_sku.get(identity)
                    or links.by_sku.get(identity)
                    or (
                        links.explicit_by_sku.get((identifier, "")) or links.by_id.get(identifier, set())
                        if product_rows[identifier] == 1
                        else set()
                    )
                )
                if zone_identity is None:
                    candidates = set()
                url = next(iter(candidates)) if len(candidates) == 1 else None
            if row.get("source") != "ld+json" and url not in links.observed:
                url = None
            try:
                validate_url(url)
                if row.get("source") == "ld+json":
                    # Vendor IDs include the query; derive only from the validated
                    # path while keeping the exact explicit source URL untouched.
                    row["product_id"] = urlsplit(url).path.rsplit("/", 1)[-1]
                if not links.matches(url, str(row.get("product_id") or ""), str(row.get("sku_id") or "")):
                    raise ValueError("Source URL does not match product/SKU")
            except ValueError:
                skipped_urls += 1
                result["warnings"].append("Skipped product with unsafe or missing source URL")
                continue
            key = (row.get("product_id"), row.get("sku_id"), url)
            if key in seen:
                continue
            seen.add(key)
            result["items"].append(product(row, url, False, 0))
            if len(result["items"]) == limit:
                break
        result["returned"] = len(result["items"])
        if skipped_urls:
            result["status"] = "partial" if result["items"] else "error"
            if not result["items"]:
                result["error"] = error("parser_drift", "Search products lacked safe reusable source URLs")
        if status == "empty":
            result["warnings"].append("Explicit empty-result banner observed")
        if any(p["price_conditional_rub"] is not None for p in result["items"]):
            result["warnings"].append(CONDITIONAL_LABEL)
        return result
