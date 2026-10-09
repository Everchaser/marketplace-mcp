"""Provider registry and stable response envelopes."""

from typing import Protocol

from .errors import MarketplaceError


class MarketplaceProvider(Protocol):
    async def search(self, query: str, limit: int, page: int) -> dict: ...
    async def get_card(self, url: str, include_reviews: bool, review_limit: int) -> dict: ...


def error(code, message, retryable=False):
    return {"code": code, "message": message, "retryable": retryable}


class MarketplaceService:
    def __init__(self, providers: dict[str, MarketplaceProvider]):
        self.providers = providers

    async def search(self, query, marketplace="yandex_market", limit=10, page=1):
        result = {
            "marketplace": marketplace,
            "status": "error",
            "query": query,
            "items": [],
            "returned": 0,
            "page": page,
            "has_next_page": None,
            "total_available": None,
            "warnings": [],
            "extraction": None,
            "error": error("unsupported_marketplace", f"Unsupported marketplace: {marketplace}"),
        }
        if marketplace not in self.providers:
            return result
        if (
            not isinstance(query, str)
            or not 2 <= len(query.strip()) <= 200
            or not bounded(limit, 1, 48)
            or not bounded(page, 1, 30)
        ):
            result["error"] = error("validation_error", "query: 2..200 characters; limit: 1..48; page: 1..30")
            return result
        result["error"] = None
        try:
            result.update(await self.providers[marketplace].search(query.strip(), limit, page))
        except MarketplaceError as exc:
            result["error"] = exc.as_dict()
            return result
        except Exception:  # noqa: BLE001 - isolate provider failures at the stable response boundary
            result["error"] = error(
                "provider_error", "Provider failed unexpectedly; no raw page or exception exposed"
            )
            return result
        result["query"] = query.strip()
        return result

    async def get_card(self, url, marketplace="yandex_market", include_reviews=True, review_limit=10):
        result = {
            "marketplace": marketplace,
            "status": "error",
            "url": url,
            "product": None,
            "warnings": [],
            "error": None,
        }
        if marketplace not in self.providers:
            result["error"] = error("unsupported_marketplace", f"Unsupported marketplace: {marketplace}")
            return result
        try:
            if type(include_reviews) is not bool or not bounded(review_limit, 0, 20):
                raise ValueError("review_limit: 0..20; include_reviews: boolean")
            # Optional provider-owned policy; the registry is marketplace-agnostic.
            validate_card_url = getattr(self.providers[marketplace], "validate_card_url", None)
            if validate_card_url is not None:
                validate_card_url(url)
        except ValueError as exc:
            result["error"] = error("validation_error", str(exc))
            return result
        try:
            result.update(await self.providers[marketplace].get_card(url, include_reviews, review_limit))
        except MarketplaceError as exc:
            result["error"] = exc.as_dict()
        except Exception:  # noqa: BLE001 - isolate provider failures at the stable response boundary
            result["error"] = error(
                "provider_error", "Provider failed unexpectedly; no raw page or exception exposed"
            )
        return result

    async def get_cards(
        self,
        urls,
        marketplace="yandex_market",
        include_reviews=True,
        review_limit=10,
        quantities=None,
        units=None,
        max_extra_shops=3,
    ):
        import asyncio

        result = {
            "marketplace": marketplace,
            "status": "error",
            "requested": len(urls) if isinstance(urls, list) else 0,
            "returned": 0,
            "failed": 0,
            "results": [],
            "warnings": [],
            "error": None,
        }
        if marketplace not in self.providers:
            result["error"] = error("unsupported_marketplace", f"Unsupported marketplace: {marketplace}")
            return result
        if (
            not isinstance(urls, list)
            or not 1 <= len(urls) <= 10
            or type(include_reviews) is not bool
            or not bounded(review_limit, 0, 20)
            or not bounded(max_extra_shops, 0, 3)
        ):
            result["error"] = error(
                "validation_error",
                "urls: 1..10; review_limit: 0..20; include_reviews: boolean; max_extra_shops: integer 0..3",
            )
            return result
        from .estimates import estimate, validate_quantities

        try:
            parsed = validate_quantities(urls, quantities, units)
        except ValueError:
            result["error"] = error(
                "validation_error",
                "quantities: matching 1..10 positive decimal strings, max 1000000, 6 decimals; units: unit (integer quantity) or kg",
            )
            return result
        gate = asyncio.Semaphore(2)

        async def one(url):
            async with gate:
                return await self.get_card(url, marketplace, include_reviews, review_limit)

        batch = getattr(self.providers[marketplace], "get_cards", None)
        if batch is None:
            result["results"] = await asyncio.gather(*(one(url) for url in urls))
        else:
            try:
                result.update(await batch(urls, include_reviews, review_limit, max_extra_shops))
            except Exception as exc:  # noqa: BLE001 - stable provider boundary
                failure = (
                    exc.as_dict()
                    if isinstance(exc, MarketplaceError)
                    else error(
                        "provider_error", "Provider failed unexpectedly; no raw page or exception exposed"
                    )
                )
                result["results"] = [
                    {
                        "marketplace": marketplace,
                        "status": "error",
                        "url": url,
                        "product": None,
                        "warnings": [],
                        "error": failure,
                    }
                    for url in urls
                ]
        allocations = result.pop("_estimate_allocations", None)
        result["returned"] = sum(r["status"] != "error" for r in result["results"])
        result["failed"] = len(urls) - result["returned"]
        result["status"] = "ok" if not result["failed"] else "partial" if result["returned"] else "error"
        if parsed is not None:
            result["estimate"] = estimate(result["results"], parsed, marketplace, allocations=allocations)
        return result


def bounded(value, low, high):
    return type(value) is int and low <= value <= high
