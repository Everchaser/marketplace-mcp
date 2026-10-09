# Independent-review logical fixes: vertical RED–GREEN evidence

Repository/artifact: `/home/everchaser/Projects/marketplace-mcp`.
All commands below ran from that directory. Results are actual tool execution,
not reconstructed marketplace data. No commits were made. This directory is not
currently a Git repository (`git status --short` returned exit 128).

## Scope and changes

Modified only the owned production/test files, plus this requested evidence file:

- `src/marketplace_mcp/core.py`: remove universal Yandex URL validation; use the
  selected provider's optional synchronous `validate_card_url` policy. Providers
  without that hook retain dispatch compatibility. Common review bounds and
  unsupported-marketplace rejection still run before provider I/O.
- `src/marketplace_mcp/yandex.py`: implement the Yandex-owned card URL policy and
  validate direct adapter card calls before transport. Associate observed anchor
  and explicit source-field URLs with product/SKU/snippet/offer identity. Resolve
  only unique matching observed URLs, preserve original queries, and reject
  ambiguous/missing/mismatched source URLs rather than trusting vendor-generated
  routes. Use explicit schema.org item URLs, snippet URL fields, and matched SSR
  show-place/offer URLs without synthesizing routes. Scoped offer links take
  precedence over less-specific family metadata. Missing URLs yield partial/error
  envelopes rather than invented successful items.
- `src/marketplace_mcp/cache.py`: share expiration purging between cache lookup and
  `size`, making `size` report live completed entries without another lookup.
  Serving, coalescing, cancellation protection, and inflight accounting are unchanged.
- `tests/test_core.py`, `tests/test_adapter.py`, `tests/test_cache.py`: regression
  coverage for these issues; existing test provider double now owns its explicit
  Yandex URL policy rather than depending on core's marketplace-specific policy.

No edits to live Hermes config, the old project, README, pyproject, vendored SSR,
transport/relay, test_server, or the stdio harness. Exactly-three-tools API and
public get_card signatures remain unchanged. Unrelated zero normalization was
intentionally not changed.

## Baseline

```console
.venv/bin/python -m pytest -q
94 passed in 2.74s
```

The shell's unqualified Python did not have pytest installed. Existing project
`.venv/bin/python` was used; no dependency/config changes were needed.

## Vertical tracer bullets

For every entry below, the test was written, the listed RED command was run and
failed for the stated behavior, then the minimal implementation was made and the
listed scoped GREEN run completed before moving to the next behavior.

Common command prefixes (the actual commands used these expanded paths):

- RED: `.venv/bin/python -m pytest <node> -q`
- Core GREEN: `.venv/bin/python -m pytest tests/test_core.py -q`
- Core/adapter GREEN: `.venv/bin/python -m pytest tests/test_core.py tests/test_adapter.py -q`
- All owned GREEN: `.venv/bin/python -m pytest tests/test_core.py tests/test_adapter.py tests/test_cache.py -q`

| Order | RED node (exact argument to pytest) | Actual RED result / predicate | Actual following GREEN |
|---|---|---|---|
| 1 | `tests/test_core.py::test_selected_provider_owns_card_url_policy` | 2 failed: non-Yandex provider URLs incorrectly returned `status=error`, both with/without optional policy | Core: 18 passed; full suite: 96 passed |
| 2 | `tests/test_adapter.py::test_yandex_card_rejects_unsafe_url_before_transport` | 1 failed: direct Yandex adapter did not raise ValueError before I/O | Core/adapter: 35 passed |
| 3 | `tests/test_adapter.py::test_zone_variants_keep_each_snippet_source_url_and_query` | 1 failed: SKU 789 incorrectly received SKU 456's first link/query | Core/adapter: 36 passed |
| 4 | `tests/test_adapter.py::test_search_rejects_parser_generated_urls_without_observed_source` | 3 failed: zone, SSR product, SSR slug fallbacks returned `ok` with invented URL | Core/adapter: 39 passed |
| 5 | `tests/test_adapter.py::test_zone_source_links_match_snippet_identity_when_path_is_sku` | 1 failed: legitimate scoped URLs ending in SKU rather than product ID were dropped | Core/adapter: 40 passed |
| 6 | `tests/test_adapter.py::test_search_does_not_choose_first_ambiguous_or_wrong_variant_link` | 3 failed: inside/outside ambiguous family links and wrong-SKU link selected rather than rejected | Core/adapter: 43 passed |
| 7 | `tests/test_adapter.py::test_explicit_source_product_url_is_not_replaced_by_card_variant` | 2 failed: exact JSON-LD and SSR product URLs were lost despite explicit source provenance | Core/adapter: 45 passed |
| 8 | `tests/test_adapter.py::test_zone_explicit_urls_are_matched_to_correct_sku_not_product_only` | 3 failed: product-only JSON-LD map assigned SKU 789 URL to SKU 456; query-bearing LD and explicit snippet URLs were dropped | Core/adapter: 48 passed |
| 9 | `tests/test_cache.py::test_cache_size_excludes_expired_entries_without_lookup` | 1 failed: size remained 1 after TTL .005 and sleep .02, with no lookup | All owned: 53 passed |
| 10 | `tests/test_adapter.py::test_zone_distinct_offers_with_same_sku_keep_their_own_links` (initial anchor-only test) | 1 failed: two wareId offers sharing SKU were conflated/treated as ambiguous | All owned: 54 passed |
| 11 | `tests/test_adapter.py::test_missing_variant_url_is_skipped_instead_of_borrowing_product_family_link` | 2 failed, 1 passed: missing row borrowed another snippet or product-only LD family link; unscoped ambiguity case already rejected | All owned: 57 passed; ruff passed |
| 12 | `tests/test_adapter.py::test_snippet_source_url_must_match_its_product_and_sku` | 3 failed: unrelated product or wrong-SKU scoped anchor/payload URL accepted | All owned: 60 passed |
| 13 | `tests/test_adapter.py::test_ssr_variant_cannot_borrow_explicit_url_from_another_offer` | 1 failed: invented fallback for SKU 789 matched explicit source belonging only to SKU 456 | All owned: 61 passed |
| 14 | `tests/test_adapter.py::test_ssr_offer_source_urls_are_reused_exactly_for_each_sku` | 1 failed: correct SSR offer URLs were ignored when direct show-place URL absent | All owned: 62 passed |
| 15 | `tests/test_adapter.py::test_zone_distinct_offers_with_same_sku_keep_their_own_links` (expanded anchor/payload/anchor_with_ld parametrization) | 2 failed, 1 passed: offer-specific snippet URL fields were merged; family LD replaced exact observed offer anchors. Original anchor case remained green | All owned: 64 passed |
| 16 | `tests/test_adapter.py::test_actual_observed_product_route_is_allowed_without_explicit_json_url` | 2 failed: actual observed product/product-- anchor routes were unnecessarily rejected | All owned: 66 passed |

Mixed RED outcomes above are stated honestly: passing parameter cases were
additional compatibility checks; the failing cases were verified before their
corresponding production change. Existing JSON-LD exact card URL tests remained
part of each adapter GREEN run. Formatting was done only after GREEN.

## Final scoped verification

```console
.venv/bin/python -m pytest tests/test_core.py tests/test_adapter.py tests/test_cache.py -q
..................................................................       [100%]
66 passed in 0.24s

.venv/bin/ruff check src/marketplace_mcp/core.py src/marketplace_mcp/yandex.py src/marketplace_mcp/cache.py tests/test_core.py tests/test_adapter.py tests/test_cache.py
All checks passed!

.venv/bin/ruff format --check src/marketplace_mcp/core.py src/marketplace_mcp/yandex.py src/marketplace_mcp/cache.py tests/test_core.py tests/test_adapter.py tests/test_cache.py
6 files already formatted
```

## Full-suite verification and cross-owner blocker

The full suite was run repeatedly, not merely the owned files. The initial
baseline was 94 passing; after the core slice the full suite was 96 passing.
Concurrent transport/relay work added tests during this task.

Latest actual full-suite result:

```console
.venv/bin/python -m pytest -q
FAILED tests/test_server.py::test_actual_stdio_initialize_list_and_structured_mocked_output
1 failed, 139 passed in 3.51s
```

The remaining failure is outside the owned changes: the stdio harness injects
`httpx.Response(200, text=HTML)`, which is already buffered/consumed. The other
owner's evolving SafeTransport uses `response.aiter_raw()` and raises
`httpx.StreamConsumed` at transport.py:82 for that fixture. The service correctly
isolates the exception as `provider_error`, so test_server's expected `ok` fails.
This was reproduced directly with SafeTransport + HTTPX MockTransport, separately
from core/Yandex parser behavior. No transport, harness, or test_server files were
modified here. Parent/network owner needs to resolve this fixture/transport
compatibility, then rerun full tests. Earlier full-suite runs also saw a transient
compression-test mismatch while transport was being changed; it is not present
in the latest full-suite result.

## Real-capture offline provenance check

Parent independently captured real public HTML in scratch:

- `/home/everchaser/.hermes/cache/scratch/marketplace-source-capture/search.html`
- `/home/everchaser/.hermes/cache/scratch/marketplace-source-capture/card.html`

Those HTML files were not added to the artifact. Regression fixtures in the test
suite are explicitly synthetic, not represented as live data.

Executed:

```console
.venv/bin/python /home/everchaser/.hermes/cache/scratch/marketplace-source-capture/verify_logic_offline.py
# exit 0
```

Actual offline replay result:

- Search: status `ok`, extraction `zone`, returned 8.
- All eight returned URLs were observed in the independently captured HTML or its
  explicit source metadata and matched their row product/SKU identity.
- Exact observed query-bearing anchors, including offer parameters, were retained.
- Card: status `ok`; exact explicit schema.org source URL retained; reviews disabled
  returned 0. This was offline replay, not a new network request.

Full machine-readable result and reproducible verifier remain in scratch:

- `/home/everchaser/.hermes/cache/scratch/marketplace-source-capture/logic-offline-result.json`
- `/home/everchaser/.hermes/cache/scratch/marketplace-source-capture/verify_logic_offline.py`
