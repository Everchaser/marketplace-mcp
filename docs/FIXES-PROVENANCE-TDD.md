# Second-review provenance fixes: vertical RED–GREEN evidence

All commands ran in `/home/everchaser/Projects/marketplace-mcp` with the existing
project virtualenv. No commits, dependency changes, live configuration changes,
or vendor edits were made. The directory is not a Git repository (`git status
--short` returned exit 128); this did not block implementation or verification.

## Minimal production changes

- Remove the positional join between `_SourceLinks`' independently parsed HTML
  scope list and vendor search rows. The adapter now recovers full
  `(product_id, sku_id, wareId)` identities from the **same `ssr.zone_snippets`
  extractor used by `ssr.parse_zone_items`**, using the vendor's exact first-seen
  full-identity deduplication. Row count and product/SKU consistency must hold
  before attaching an identity. HTMLParser URL scopes are then looked up by that
  verified full identity, never by an independent scope ordinal. Inconsistent
  rows fail closed; surviving valid rows retain partial-result semantics.
- Normalize LD+JSON's URL-derived product ID from `urlsplit(url).path` only after
  validating the selected source URL. Preserve the source URL and query verbatim.
- Recheck every final selected URL against its returned row's product/SKU,
  including LD+JSON, before returning it. Wrong variants are skipped, yielding
  `partial` or `error`, not a successful wrong-variant item.

Changed only `src/marketplace_mcp/yandex.py`, `tests/test_adapter.py`, and this
new evidence file. Existing test expectations were not changed. The 933-line
MIT vendor parser, core/cache, transport/compression/DNS, and live config were
not edited. No architecture expansion or generated HTML fixtures were added.

## Vertical TDD execution

Each production change followed its own observed failure and passing scoped
run before the next failing test was added. Commands below are actual executions,
not reconstructed output. Test command timeout was 60 seconds.

| Slice | RED command after adding regression | Actual failure | Following GREEN |
|---|---|---|---|
| Independent HTML extractors | `.venv/bin/python -m pytest tests/test_adapter.py::test_zone_source_identity_ignores_snippets_not_parsed_by_vendor -q` | **3 failed in 0.11s**. Product `333`, SKU `444` received `/card/First/111?sku=222` instead of `/card/Second/333?sku=444`. Exact first-single-quoted/second-double-quoted repro plus single-data-quoted and uppercase-attribute variants. | `.venv/bin/python -m pytest tests/test_adapter.py -q`: **46 passed in 0.08s** |
| LD+JSON URL-derived ID | `.venv/bin/python -m pytest tests/test_adapter.py::test_ldjson_product_id_comes_from_path_without_changing_source_query -q` | **3 failed in 0.11s**. ID was `123?sku=456&offer=a%2Bb`, not string `123`, on card, product, and product-- paths. | `.venv/bin/python -m pytest tests/test_adapter.py -q`: **49 passed in 0.09s** |
| Extend final identity guard to LD+JSON | `.venv/bin/python -m pytest tests/test_adapter.py::test_ldjson_final_url_guard_rejects_wrong_sku -q` | **2 failed in 0.10s**. Explicit SKU `456` with URL query SKU `789` incorrectly returned `ok`, rather than `error` alone or `partial` alongside a valid item. | `.venv/bin/python -m pytest tests/test_adapter.py -q`: **51 passed in 0.09s** |

Six additional compatibility/defensive cases were then added and passed without
further production changes: deduplication plus two distinct offers sharing
product/SKU after an ignored single-quoted snippet; row product, SKU, and count
drift; and final-candidate product/SKU corruption. These supplemental cases were
not represented as additional RED failures. Existing scoped offer, SKU-path,
explicit URL, exact query, ambiguity, and missing-variant tests stayed intact.

## Final verification

After GREEN, Ruff formatted only the two owned Python files. Its initial format
check identified formatting changes; these were applied and verified.

```console
.venv/bin/ruff check .
All checks passed!

.venv/bin/ruff format --check src/marketplace_mcp/yandex.py tests/test_adapter.py
2 files already formatted

.venv/bin/python -m pytest -q
154 passed in 3.55s
```

All original 140 tests plus 14 added parameterized regression/defensive cases
pass. Full-suite timeout was 120 seconds.

An additional read-only offline replay used the parent's anonymous capture at
`/home/everchaser/.hermes/cache/scratch/marketplace-source-capture/search.html`.
Actual output: status `ok`, extraction `zone`, returned **8**; every returned URL
was observed in the captured source, passed URL validation, and matched its
returned product/SKU. This was not a new live request, and neither the capture nor
new scratch artifacts were written or shipped. Parent owns subsequent live
smoke and independent final review.
