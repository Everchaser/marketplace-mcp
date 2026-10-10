"""CLI: stdout is reserved exclusively for MCP JSON-RPC."""

import utils.transport
import utils.session
import argparse
from contextlib import nullcontext

from .core import MarketplaceService
from .geocoding import PublicGeocoder
from .relay import YandexRelay
from .server import create_server
from .settings import load_settings
from .transport import SafeTransport
from .varus import VarusProvider
from .varus_transport import VarusTransport
from .yandex import YandexMarketProvider


def main():
    parser = argparse.ArgumentParser(description="Read-only marketplace MCP over stdio")
    parser.add_argument("--nic", help="Optional Linux interface for Yandex-only direct routing")
    parser.add_argument(
        "--settings", help="Shopping JSON settings (default: XDG_CONFIG_HOME/shopping/settings.json)"
    )
    args = parser.parse_args()
    try:
        with YandexRelay(args.nic) if args.nic else nullcontext(None) as relay:
            transport = SafeTransport(proxy=relay.proxy_url if relay else None)
            service = MarketplaceService(
                {
                    "yandex_market": YandexMarketProvider(transport),
                    "varus": VarusProvider(VarusTransport(), load_settings(args.settings), PublicGeocoder()),
                }
            )
            create_server(service).run(transport="stdio")
    except (OSError, ValueError):
        parser.exit(2, "marketplace-mcp: NIC relay or configuration error; no routing fallback\n")


if __name__ == "__main__":
    main()
