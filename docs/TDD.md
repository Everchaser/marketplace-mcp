# Vertical TDD execution evidence

All commands below were executed in `/home/everchaser/Projects/marketplace-mcp` using Python 3.12.14.
Tests use intentionally synthetic HTML and mock HTTP transport only inside `tests/`.
The actual stdio integration launches a subprocess, initializes MCP, lists exactly three tools, and checks structured content.

`uv sync --python 3.12`: exit 0 (mcp 1.30.0, httpx 0.28.1).
Final verification: **94 passed**; Ruff check and format check exit 0; locked sync exit 0; wheel/sdist build exit 0.

A first attempted GREEN for the SDK schema failed because plain `dict` is not accepted for structured output by the installed SDK. It was corrected to `dict[str, Any]`; the subsequent full suite passed. Existing-behavior relay shutdown/header checks passed immediately and are labeled POST-GREEN rather than a new feature cycle.

## RED unsupported marketplace

`uv run pytest tests/test_core.py -q` — exit **1**

```text
E   ModuleNotFoundError: No module named 'marketplace_mcp'
1 failed in 0.03s
```

## GREEN unsupported marketplace

`uv run pytest tests -q` — exit **0**

```text
1 passed in 0.01s
```

## RED search validation and dispatch

`uv run pytest tests/test_core.py -q` — exit **1**

```text
E       AssertionError: assert 'unsupported_marketplace' == 'validation_error'
8 failed, 1 passed in 0.05s
```

## GREEN search validation and dispatch

`uv run pytest tests -q` — exit **0**

```text
9 passed in 0.02s
```

## RED strict URLs

`uv run pytest tests/test_urls.py -q` — exit **1**

```text
E   ModuleNotFoundError: No module named 'marketplace_mcp.urls'
17 failed in 0.10s
```

## GREEN strict URLs

`uv run pytest tests -q --tb=short` — exit **0**

```text
26 passed in 0.03s
```

## RED card validation

`uv run pytest tests/test_core.py::test_card_validation_before_io -q --tb=short` — exit **1**

```text
E   AttributeError: 'MarketplaceService' object has no attribute 'get_card'
1 failed in 0.03s
```

## GREEN card validation

`uv run pytest tests -q --tb=short` — exit **0**

```text
27 passed in 0.03s
```

## RED ordered bounded batch

`uv run pytest tests/test_core.py -k batch -q --tb=short` — exit **1**

```text
E   AttributeError: 'MarketplaceService' object has no attribute 'get_cards'. Did you mean: 'get_card'?
4 failed, 10 deselected in 0.05s
```

## GREEN ordered bounded batch

`uv run pytest tests -q --tb=short` — exit **0**

```text
31 passed in 0.08s
```

## RED normalized SSR card

`uv run pytest tests/test_adapter.py -q --tb=short` — exit **1**

```text
E   ModuleNotFoundError: No module named 'marketplace_mcp.yandex'
1 failed in 0.03s
```

## GREEN normalized SSR card

`uv run pytest tests -q --tb=short` — exit **0**

```text
32 passed in 0.09s
```

## RED card failure and conditional semantics

`uv run pytest tests/test_adapter.py -k "failure or promote" -q --tb=short` — exit **1**

```text
E   assert 90.0 is None
4 failed, 1 deselected in 0.05s
```

## GREEN card failure and conditional semantics

`uv run pytest tests -q --tb=short` — exit **0**

```text
36 passed in 0.08s
```

## RED SSR search

`uv run pytest tests/test_adapter.py -k search -q --tb=short` — exit **1**

```text
E   AttributeError: 'YandexMarketProvider' object has no attribute 'search'
4 failed, 5 deselected in 0.05s
```

## GREEN SSR search

`uv run pytest tests -q --tb=short` — exit **0**

```text
40 passed in 0.09s
```

## RED safe HTTP fetch

`uv run pytest tests/test_transport.py -q --tb=short` — exit **1**

```text
E   ModuleNotFoundError: No module named 'marketplace_mcp.transport'
1 failed in 0.16s
```

## GREEN safe HTTP fetch

`uv run pytest tests -q --tb=short` — exit **0**

```text
41 passed in 0.12s
```

## RED HTTP errors body bounds

`uv run pytest tests/test_transport.py -k "errors or cap" -q --tb=short` — exit **1**

```text
E   Failed: DID NOT RAISE Exception
6 failed, 1 deselected in 0.08s
```

## GREEN HTTP errors body bounds

`uv run pytest tests -q --tb=short` — exit **0**

```text
47 passed in 0.13s
```

## RED bounded transient retries

`uv run pytest tests/test_transport.py -k "transient or exhausted" -q --tb=short` — exit **1**

```text
E   marketplace_mcp.errors.MarketplaceError: Upstream HTTP 302
6 failed, 7 deselected in 0.15s
```

## GREEN bounded transient retries

`uv run pytest tests -q --tb=short` — exit **0**

```text
53 passed in 0.14s
```

## RED safe bounded redirects

`uv run pytest tests/test_transport.py -k redirect -q --tb=short` — exit **1**

```text
E   AssertionError: assert 'transient_http' == 'unsafe_redirect'
6 failed, 13 deselected in 0.10s
```

## GREEN safe bounded redirects

`uv run pytest tests -q --tb=short` — exit **0**

```text
59 passed in 0.15s
```

## RED shared request pacing

`uv run pytest tests/test_transport.py -k pacing -q --tb=short` — exit **1**

```text
E   assert False
1 failed, 19 deselected in 0.07s
```

## GREEN shared request pacing

`uv run pytest tests -q --tb=short` — exit **0**

```text
60 passed in 0.22s
```

## RED bounded TTL coalescing cache

`uv run pytest tests/test_cache.py -q --tb=short` — exit **1**

```text
E   ModuleNotFoundError: No module named 'marketplace_mcp.cache'
3 failed in 0.04s
```

## GREEN bounded TTL coalescing cache

`uv run pytest tests -q --tb=short` — exit **0**

```text
63 passed in 0.30s
```

## RED HTTP cache integration

`uv run pytest tests/test_transport.py -k http_cache -q --tb=short` — exit **1**

```text
E   assert 3 == 2
1 failed, 20 deselected in 0.07s
```

## GREEN HTTP cache integration

`uv run pytest tests -q --tb=short` — exit **0**

```text
64 passed in 0.31s
```

## RED structured provider failures

`uv run pytest tests/test_core.py -k provider_failures -q --tb=short` — exit **1**

```text
E   marketplace_mcp.errors.MarketplaceError: network failed
1 failed, 14 deselected in 0.04s
```

## GREEN structured provider failures

`uv run pytest tests -q --tb=short` — exit **0**

```text
65 passed in 0.32s
```

## RED official FastMCP tool schema

`uv run pytest tests/test_server.py -q --tb=short` — exit **1**

```text
E   ModuleNotFoundError: No module named 'marketplace_mcp.server'
1 failed in 0.83s
```

## GREEN official FastMCP tool schema

`uv run pytest tests -q --tb=short` — exit **1**

```text
E   mcp.server.fastmcp.exceptions.InvalidSignature: Function search: return type <class 'dict'> is not serializable for structured output
1 failed, 65 passed in 0.72s
```

## RED stdio integration and module CLI

`uv run pytest tests/test_server.py -k "stdio or module" -q --tb=short` — exit **1**

```text
E   assert (1 == 0)
2 failed, 1 deselected in 0.99s
```

## GREEN official schema stdio and CLI

`uv run pytest tests -q --tb=short` — exit **0**

```text
68 passed in 1.77s
```

## RED restricted local relay

`uv run pytest tests/test_relay.py -q --tb=short` — exit **1**

```text
E   ModuleNotFoundError: No module named 'marketplace_mcp.relay'
2 failed in 0.03s
```

## GREEN restricted local relay

`uv run pytest tests -q --tb=short` — exit **0**

```text
70 passed in 1.76s
```

## RED bound socket and tunneled relay

`uv run pytest tests/test_relay.py -k "socket_binding or allowed" -q --tb=short` — exit **1**

```text
E   AttributeError: module 'marketplace_mcp.relay' has no attribute 'connect_bound'
2 failed, 2 deselected in 0.04s
```

## GREEN bound socket and tunneled relay

`uv run pytest tests -q --tb=short` — exit **0**

```text
72 passed in 1.90s
```

## RED NIC CLI lifecycle

`uv run pytest tests/test_server.py -k "invalid_nic or uses_relay" -q --tb=short` — exit **1**

```text
E   AssertionError: assert 0 == 2
2 failed, 3 deselected in 0.96s
```

## GREEN NIC CLI lifecycle

`uv run pytest tests -q --tb=short` — exit **0**

```text
74 passed in 2.40s
```

## GREEN refactor formatting and lint

`uv run pytest tests -q --tb=short` — exit **0**

```text
74 passed in 2.49s
```

## RED source links values drift and pagination

`uv run pytest tests/test_adapter.py -k "zone_search or values_drift or pagination_unknown" -q --tb=short` — exit **1**

```text
E   AssertionError: assert 'https://mark...u/product/123' == 'https://mark...e/123?sku=456'
3 failed, 9 deselected in 0.06s
```

## GREEN source links values drift and pagination

`uv run pytest tests -q --tb=short` — exit **0**

```text
77 passed in 2.41s
```

## RED per-item unexpected provider failure

`uv run pytest tests/test_core.py -k unexpected_provider -q --tb=short` — exit **1**

```text
E   TypeError: raw secret error
1 failed, 15 deselected in 0.05s
```

## GREEN per-item unexpected provider failure

`uv run pytest tests -q --tb=short` — exit **0**

```text
78 passed in 2.44s
```

## RED malformed SSR taxonomy

`uv run pytest tests/test_adapter.py -k malformed -q --tb=short` — exit **1**

```text
E   AttributeError: 'list' object has no attribute 'get'
2 failed, 12 deselected in 0.10s
```

## GREEN malformed SSR taxonomy

`uv run pytest tests -q --tb=short` — exit **0**

```text
80 passed in 2.44s
```

## RED ambiguous URL encoding

`uv run pytest tests/test_urls.py -k ambiguous -q --tb=short` — exit **1**

```text
E   Failed: DID NOT RAISE ValueError
3 failed, 1 passed, 17 deselected in 0.03s
```

## GREEN ambiguous URL encoding

`uv run pytest tests -q --tb=short` — exit **0**

```text
84 passed in 2.49s
```

## RED shared transport concurrency

`uv run pytest tests/test_transport.py -k transport_concurrency -q --tb=short` — exit **1**

```text
E   assert 6 == 2
1 failed, 21 deselected in 0.09s
```

## GREEN shared transport concurrency

`uv run pytest tests -q --tb=short` — exit **0**

```text
85 passed in 2.51s
```

## RED whole request deadline

`uv run pytest tests/test_transport.py -k deadline -q --tb=short` — exit **1**

```text
E   Failed: DID NOT RAISE MarketplaceError
1 failed, 22 deselected in 0.11s
```

## GREEN whole request deadline

`uv run pytest tests -q --tb=short` — exit **0**

```text
86 passed in 2.50s
```

## RED atomic cache capacity

`uv run pytest tests/test_cache.py -k combined -q --tb=short` — exit **1**

```text
E   assert 3 <= 2
1 failed, 3 deselected in 0.04s
```

## GREEN atomic cache capacity

`uv run pytest tests -q --tb=short` — exit **0**

```text
87 passed in 2.51s
```

## POST-GREEN regression checks: existing relay shutdown/header bounds

`uv run pytest tests/test_relay.py -k "partial_header or header_size" -q --tb=short` — exit **0**

```text
2 passed, 4 deselected in 0.23s
```

## RED relay absolute header deadline

`uv run pytest tests/test_relay.py -k absolute_deadline -q --tb=short` — exit **1**

```text
E   AttributeError: <module 'marketplace_mcp.relay' from '/home/everchaser/Projects/marketplace-mcp/src/marketplace_mcp/relay.py'> has no attribute 'HEADER_TIMEOUT'
1 failed, 6 deselected in 0.04s
```

## GREEN relay absolute header deadline

`uv run pytest tests -q --tb=short` — exit **0**

```text
90 passed in 2.76s
```

## FINAL suite after refactor

`uv run pytest tests -q --tb=short` — exit **0**

```text
90 passed in 2.73s
```

## RED captcha redirect classification

`uv run pytest tests/test_transport.py -k captcha_redirect -q --tb=short` — exit **1**

```text
E   AssertionError: assert ('unsafe_redirect' == 'captcha'
1 failed, 23 deselected in 0.08s
```

## GREEN captcha redirect classification

`uv run pytest tests -q --tb=short` — exit **0**

```text
91 passed in 2.76s
```

## FINAL formatted locked suite

`uv run pytest tests -q --tb=short` — exit **0**

```text
91 passed in 2.80s
```

## RED unsafe result URLs and unknown review values

`uv run pytest tests/test_adapter.py -k "unsafe_search or unknown_review" -q --tb=short` — exit **1**

```text
E   AssertionError: assert ('ok' == 'error'
2 failed, 14 deselected in 0.06s
```

## GREEN unsafe result URLs and unknown review values

`uv run pytest tests -q --tb=short` — exit **0**

```text
93 passed in 2.65s
```

## RED same-interface alternative resolved addresses

`uv run pytest tests/test_relay.py -k network_address_failure -q --tb=short` — exit **1**

```text
E   OSError: [Errno 101] Network unreachable
1 failed, 7 deselected in 0.05s
```

## GREEN same-interface alternative resolved addresses

`uv run pytest tests -q --tb=short` — exit **0**

```text
94 passed in 2.72s
```

## FINAL tests

`uv run pytest tests -q --tb=short` — exit **0**

```text
94 passed in 2.70s
```
