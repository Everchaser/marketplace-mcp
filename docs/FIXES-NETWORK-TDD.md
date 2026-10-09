# Transport allocation and relay DNS fixes — vertical TDD

Scope: `src/marketplace_mcp/transport.py`, `src/marketplace_mcp/relay.py`, their two test modules, and this requested evidence record. No live configuration, old project, vendor parser, MCP schema, manifest, or other agent-owned implementation was changed by this work.

## Root causes

* `Response.aiter_bytes()` invokes the content decoder before yielding to the body-length check. A gzip chunk can therefore allocate its entire expanded representation before the check rejects it. A cap after decoding is not a pre-allocation bound.
* `socket.getaddrinfo()` was synchronous on the non-daemon CONNECT handler. The connection deadline was only inspected after DNS returned, and `server_close()` joined that handler indefinitely when DNS stalled.

## Sequential RED → GREEN slices

Each production change followed its own observed failing regression, not a batch of implementation-first changes. Logs below are under `/home/everchaser/.hermes/cache/scratch/`.

| Slice | Actual RED | Minimal change | Actual GREEN |
| --- | --- | --- | --- |
| Gzip allocation | `network-transport-red.log`: expected `unsupported_content_encoding`, got `body_too_large` after decoding | Request identity; reject non-identity response headers before reading; use bounded raw iteration | `network-transport-green.log`: 25 passed |
| Shutdown during stalled DNS | `network-relay-shutdown-red.log`: close thread still alive after 0.5 seconds with DNS held on an Event | Move host DNS into a daemon worker; pass server stopping Event to the handler's connect operation; poll cancellation | `network-relay-shutdown-green.log`: 9 passed |
| DNS deadline | `network-relay-deadline-red.log`: connection caller still waiting after 0.3 seconds with test deadline 0.03 seconds | Use the existing total 20-second connection budget while waiting for DNS, not just after DNS | `network-relay-deadline-green.log`: 10 passed |
| Resource exhaustion | `network-relay-capacity-red.log`: ninth stuck resolver was created and returned a deadline error instead of a capacity error | Add a process-wide eight-permit semaphore; retain permits until the actual DNS call finishes; reject excess work immediately | `network-relay-capacity-green.log`: 11 passed |
| Already stopping | `network-relay-prestop-red.log`: DNS started despite stopping Event already set | Reject already-cancelled resolution before spawning a worker | `network-relay-prestop-green.log`: 1 passed |

Commands used for RED were `.venv/bin/python -m pytest tests/test_transport.py::test_gzip_bomb_rejected_before_stream_read_or_decoding -v` and the respective named relay regressions with `-v`. GREEN ran the owning module after each main slice. The original transport/relay baseline was 32 passed.

Additional coverage extends these fixes without changing production behavior: rejected deflate/br/zstd/unknown/empty/chained encoding headers on a transient HTTP status; exact identity byte limits; a false Content-Length plus an unread stream tail; real subprocess exit with DNS permanently blocked; and eight simultaneous stalled handlers with a rejected ninth connection.

## Why the transport bounds are enforced before decompression

1. `Accept-Encoding: identity` requests an uncompressed response. This header alone is not the security boundary.
2. Before consuming any response-body bytes, an absent Content-Encoding is treated as identity; only an explicitly identity encoding (case-insensitive, surrounding whitespace allowed) is accepted. Every compressed, unknown, empty, or chained encoding fails closed with non-retryable `unsupported_content_encoding`. The stream is closed, not read or retried, even for a 503 response.
3. The accepted body is read using `aiter_raw(chunk_size=min(65536, max_bytes + 1))`; no HTTPX content decoder is invoked. Identity means the raw body-byte limit and decoded byte limit are the same limit.
4. Every chunk is checked against the remaining body capacity before `bytearray.extend()`. Content-Length is not trusted. Overflow returns non-retryable `body_too_large`; UTF-8 text conversion only occurs after the bounded byte body is complete.

The gzip regression supplies an actual `httpx.AsyncByteStream` yielding a gzip fixture that expands to 16 MiB, with max_bytes=1024. It asserts zero stream reads, stream closure, one request, identity request headers, and traced request-path peak allocation below 512 KiB. The fixture is constructed before starting tracemalloc. It does not rely on `Response(content=...)`, which HTTPX can eagerly decode before the transport sees it. Existing transport test fixtures were converted to explicit asynchronous streams for the same reason.

The raw HTTP body cap does not count TCP/TLS overhead or HTTP chunk framing. It does not claim to prevent allocations made by a caller-supplied custom transport before that transport returns a response. Normal HTTPX network streaming is used in production.

## Why stalled DNS no longer blocks shutdown or process exit

The host resolver still resolves only hardcoded `market.yandex.ru:443`; no DNS server, host setting, or routing rule was changed. A daemon resolver worker performs the potentially uninterruptible libc call. The waiting CONNECT handler checks its total connection deadline and stopping Event at most every 50 ms, so it can exit without waiting for libc DNS. The existing server closes active sockets and joins the now-cancellable non-daemon handlers. Tests keep DNS blocked throughout close and verify close returns in under 0.5 seconds, the relay thread and handler threads terminate, and the daemon DNS workers remain blocked.

A subprocess regression also exits successfully with one daemon resolver permanently stalled and produces no stdout/stderr. This proves the worker does not keep Python alive, rather than merely asserting a daemon flag.

There are at most eight concurrent host DNS calls across the process. A timed-out or cancelled waiter does not release its resolver permit; only the actual worker's completion releases it. Thus retries and relay restarts cannot create an unlimited succession of stuck resolver threads. Saturation fails closed immediately. The capacity regression keeps eight resolvers blocked, performs ten additional rejected calls, starts/closes another relay, confirms no ninth DNS call, then releases and joins the fixtures and confirms capacity is reusable. The CONNECT handler limit remains eight.

This specifically eliminates the unbounded DNS wait. TCP connection attempts retain their finite remaining connection-budget timeout; this change does not claim every possible TCP connect operation is instantaneously cancellable.

## Preserved behavior and verification

* Strict URL and redirect allowlists, four-hop maximum, three transport attempts, shared pacing, and at most two concurrent fetches remain unchanged.
* HTTP timeout remains 20 seconds, TLS verification remains enabled, and trust_env remains false.
* Every upstream socket still receives SO_BINDTODEVICE before TCP connect. Bind failure closes that socket and aborts without an unbound fallback; trying an alternative resolved IP still binds the same approved NIC. Existing order/fail-closed regressions pass.
* Relay listening remains loopback-only on an ephemeral port. The new implementation emits no stdout; JSON-RPC remains unpolluted.
* Scoped command: `.venv/bin/python -m pytest tests/test_transport.py tests/test_relay.py -q` — `network-scoped.log`: **48 passed**. Ten subsequent independent process runs also passed all 48 tests each; `network-scoped-repeat.log` records every run.
* Lint command: `.venv/bin/python -m ruff check src tests` — `network-ruff.log`: **All checks passed!**
* Full command: `.venv/bin/python -m pytest -q` — `network-full.log`: **126 passed, 2 failed** at this concurrent snapshot. One failure was the other agent's adapter offer/link regression; the other was `test_actual_stdio_initialize_list_and_structured_mocked_output` because `tests/stdio_harness.py` still constructs an eagerly consumed `httpx.Response(200, text=HTML)`. That harness is outside this task's ownership and was not edited. Its mock should return `httpx.Response(200, stream=httpx.ByteStream(HTML.encode()))`, not weaken the raw-stream production fix. The earlier snapshot is preserved in `network-full-first.log` (105 passed, 4 failed while adapter TDD was in flight).

The parent separately reported live identity requests returning HTTP 200 with absent Content-Encoding for both search and card. This subagent did not repeat that live smoke or claim an independent final review. The parent owns final live verification, the unrelated fixture update, the combined-suite rerun after concurrent work, and fresh independent review.
