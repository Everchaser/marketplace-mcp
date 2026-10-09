# Third-party notices

## Yandex SSR parser

`src/marketplace_mcp/vendor/yandex_ssr.py` is derived from:

- Project: `Vladimir-Human/ru-marketplace-mcp`
- Release: `v2.4.2`
- Git revision: `e758a4770b5625a68ab08ab3d2d55622a33a997e`
- Original path: `packages/yandex-connector/src/yandex_connector/ssr.py`
- License: MIT
- Copyright (c) 2026 Vladimir-Human

The upstream MIT license is included in `src/marketplace_mcp/vendor/LICENSE`.
This MVP reuses the standalone parser, not the upstream MCP aggregator or runtime.
The new provider interface, service and MCP tools belong to this project. The upstream parser does not determine the URL we fetch: exact search-result card URLs are passed by callers.

Dependencies are declared in `pyproject.toml` and locked in `uv.lock`; their licenses remain those of their respective authors.
