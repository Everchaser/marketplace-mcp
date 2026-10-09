"""Test-only stdio process. No injectable fake provider exists in production CLI."""

import httpx

from marketplace_mcp.core import MarketplaceService
from marketplace_mcp.server import create_server
from marketplace_mcp.transport import SafeTransport
from marketplace_mcp.yandex import YandexMarketProvider

HTML = '<script type="application/ld+json">{"@type":"Product","name":"Synthetic test only","url":"https://market.yandex.ru/card/test/123","offers":{"price":90}}</script>'
transport = SafeTransport(
    transport=httpx.MockTransport(
        lambda request: httpx.Response(200, stream=httpx.ByteStream(HTML.encode()))
    ),
    min_gap=0,
)
create_server(MarketplaceService({"yandex_market": YandexMarketProvider(transport)})).run(transport="stdio")
