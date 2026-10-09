"""Exactly three official-SDK tools; providers do not alter the tool schema."""

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .core import MarketplaceService


def create_server(service: MarketplaceService) -> FastMCP:
    mcp = FastMCP(
        "marketplace-mcp",
        log_level="WARNING",
        instructions="Read-only anonymous marketplace data. Conditional discounts are unverified; no purchases.",
    )
    annotations = ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
    )

    @mcp.tool(name="search", annotations=annotations, structured_output=True)
    async def search(
        query: str, marketplace: str = "yandex_market", limit: int = 10, page: int = 1
    ) -> dict[str, Any]:
        """Search marketplace products. Keep exact returned source URLs for card calls."""
        return await service.search(query, marketplace, limit, page)

    @mcp.tool(name="get_card", annotations=annotations, structured_output=True)
    async def get_card(
        url: str, marketplace: str = "yandex_market", include_reviews: bool = True, review_limit: int = 10
    ) -> dict[str, Any]:
        """Fetch one exact product URL. Reviews are only an SSR subset, possibly across variants."""
        return await service.get_card(url, marketplace, include_reviews, review_limit)

    @mcp.tool(name="get_cards", annotations=annotations, structured_output=True)
    async def get_cards(
        urls: list[str],
        marketplace: str = "yandex_market",
        include_reviews: bool = True,
        review_limit: int = 10,
        quantities: list[str] | None = None,
        units: list[str] | None = None,
        max_extra_shops: Annotated[int, Field(strict=True, ge=0, le=3)] = 3,
    ) -> dict[str, Any]:
        """Fetch 1..10 product URLs in input order, keeping duplicates and individual failures."""
        return await service.get_cards(
            urls, marketplace, include_reviews, review_limit, quantities, units, max_extra_shops
        )

    return mcp
