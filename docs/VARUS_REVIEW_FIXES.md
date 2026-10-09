# VARUS confirmed review blockers — fix evidence

Only the three confirmed VARUS review blockers were changed. This records executed checks, not independent review approval.

## Changes

- `src/marketplace_mcp/varus.py`: HTMLParser collects closed inline classic JavaScript script contents. SSR extraction requires exactly one candidate script, an anchored `window.__INITIAL_STATE__` assignment, JSON decoded without executing JavaScript, and a supported complete trailer. Accepted trailers are `;` and the exact script-removal trailer observed in the public VARUS capture. HTML comments and ordinary HTML text cannot supply or overshadow the script state. Commented, quoted, conditional, repeated and trailing arbitrary JS assignments fail closed. Canonical URL and SSR `url_key` matching are unchanged.
- The same parser now requires `product.current.id`, `sku`, `name`, and `url_key` before any successful card or estimate. IDs/SKUs must be positive integers or ASCII digit strings of at most 19 digits, with numeric value at most `2**63 - 1`; booleans, nulls, containers, floats, missing and invalid values are rejected. Names are nonblank strings of at most 1000 characters; URL keys are nonblank strings of at most 251 characters. Accepted values are preserved, not coerced or repaired. Existing bounded transport and body limits are unchanged.
- Shop selection requires both selected physical `id` and `tms_id` to occur exactly once across catalogue records, including records excluded from selection. Duplicate physical IDs with distinct TMS IDs fail regardless of catalogue order, explicit override or nearest-shop selection.
- `tests/test_varus.py`: regression matrix for the actual forged-comment/plain-text state reproducer, unsupported/ambiguous assignments, malformed/missing/unbounded product fields, incomplete totals for missing IDs, and order/selection-independent duplicate physical IDs.

## Vertical TDD

Evidence directory: `/home/everchaser/.hermes/cache/scratch/varus-review-fixes`.

| Slice | RED evidence | GREEN full-suite evidence |
| --- | --- | --- |
| Script provenance | `01-red.log`: 7 failures, including returned SKU `FORGED` | `01-green-full.log`: 225 passed |
| Required product identity | `02-red.log`: 36 failures, 20 already-rejected controls passed; missing ID incorrectly returned an OK batch | `02-green-full.log`: 281 passed |
| Unique physical-shop join | `03-red.log`: 8 failures | `03-green-full.log`: 289 passed |

Each slice's regression was run before that slice's implementation; each implementation was followed by the full suite. An initial Ruff TRY004 finding was resolved with `TypeError` inside the existing extraction-error boundary; `ruff-initial.log` preserves it. Final checks are `pytest-final.log` (289 passed), `ruff.log` (all checks passed), and `format.log` (40 files already formatted).

## Live stdio and distributable checks

All four smoke exercises use real public responses through MCP stdio initialize, three-tool discovery, search, single card, batch cards, mixed errors and unsupported-provider handling. VARUS also exercises ordinary/promotional estimates, unit mismatch and a kilogram product. Only `examples/shopping.sample.json` public-shop coordinates were used; no private address or live Hermes configuration was changed. The existing `--nic enp9s0` process-specific Yandex relay was used, with no host network changes; VARUS retained its existing host route.

- Source: `source-varus.json` / `source-varus.log`, `source-yandex.json` / `source-yandex.log` — passed.
- Clean wheel: `wheel-varus.json` / `wheel-varus.log`, `wheel-yandex.json` / `wheel-yandex.log` — passed.
- VARUS observed physical shop `1`, TMS `1001`; sample SKU `2656669`, ordinary `124` UAH, promotional `74.9` UAH; duplicate-card quantities 2 + 3 yield ordinary `620.00` and promotional `374.50`. These observations are not reservations or delivery guarantees.
- Yandex source and wheel each returned four search items, two successful batch cards and one isolated mixed-batch error.
- `build.log`: sdist and wheel build; `clean-env.log` and `wheel-install.log`: new scratch CPython 3.12 environment and non-editable wheel installation. Unchanged smoke scripts were copied into scratch so clean-wheel servers run from a scratch working directory without source or editable imports. `PYTHONPATH` was removed.
- `wheel-provenance.log`: installed VARUS module resolves inside the clean environment's `site-packages` and its bytes match final source. `artifacts-sha256.log`: checksums of built artifacts. `verification-summary.json`: read-back assertions and smoke call counts.

Artifacts: `dist/marketplace_mcp-0.1.0-py3-none-any.whl` and `dist/marketplace_mcp-0.1.0.tar.gz` inside the evidence directory. Pre-fix source/test copies are under `before/`; the focused diff is `review-fixes.diff`.

No git commit/push, Hermes configuration, personal settings/address, Obsidian or host network changes were made. Independent re-review remains a separate step.

## Follow-up: inert enclosing-context provenance

This follow-up fixes only the confirmed inert-context blocker; it is **not independent approval**.

- `Canonicals` tracks nested templates and excludes their scripts and canonical links. `noscript` is parsed as raw text under the scripting-enabled live-document assumption. Explicit text modes suppress apparent markup inside `style`, `title`, `textarea`, `xmp`, `iframe`, `noembed`, and `noframes`, including on older Python 3.12 parsers. `plaintext` suppresses the remainder of the document: a literal `</plaintext>` cannot restore script extraction.
- The HTML self-closing flag does not terminate these non-void containers; `<template/>` remains inert until its real end tag. Raw-text contents cannot close an enclosing template. Nested, unclosed, unrelated-end-tag and self-closing cases have regressions. Closed inert scripts before or after the actual script cannot shadow it. JSON-only state decoding and the existing executable-type/identity/uniqueness checks remain unchanged; no JavaScript evaluation or dependency additions.
- Numeric forged SKU `1234567` inside a sole template/noscript script now produces batch `error`, per-card `extraction_failed`, ordinary estimate `complete=false`, `included=0`. The synthetic regressions deliberately retain valid product identity and price fields so rejection is due to context, not malformed IDs.

Evidence directory: `/home/everchaser/.hermes/cache/scratch/varus-inert-fix`.

| Vertical slice/check | Executed evidence |
| --- | --- |
| Sole template/noscript script | `01-red.log`: 2 failures, incorrectly returned batch `ok`; before production edit. `01-green-full.log`: 291 passed. |
| Raw-text/nested/self-closing matrix | `02-red.log`: 3 failures and 37 passing controls; exposed `<template/>` and `<textarea/>` behavior before its fix. `02-green-full.log`: 331 passed. |
| Final suite including unrelated end tags | `pytest-final.log`: **335 passed in 6.79s**; preserves all previous 289 tests. |
| Ruff / format / build | `ruff.log`: all checks passed; `format.log`: 41 files already formatted; `build.log`: wheel and sdist built successfully. |

Both real VARUS and Yandex stdio smokes passed against source and against the freshly reinstalled, non-editable wheel in `wheel-env`; unchanged smoke scripts run from scratch with `PYTHONPATH` removed for wheel checks. Files: `source-varus.json/.log`, `source-yandex.json/.log`, `wheel-varus.json/.log`, `wheel-yandex.json/.log`. Each server discovered exactly `search`, `get_card`, `get_cards`. VARUS made eight tool calls per run; Yandex five. Both VARUS runs verified public sample shop `1` / TMS `1001`, SKU `2656669`, ordinary `124` UAH, promotional `74.9` UAH, quantities 2 + 3 totals `620.00` / `374.50`, and an isolated mixed-batch error. Both Yandex runs returned four search items, two batch cards and one isolated mixed-batch error.

The first source-Yandex smoke encountered an upstream CAPTCHA and failed closed (`captcha`, non-retryable); its unmodified record is preserved as `source-yandex-attempt1.json/.log`. The independent wheel run and a subsequent explicit fresh source smoke passed. No adapter retry policy or networking configuration was changed.

`clean-env.log`, `wheel-install.log`, `wheel-provenance.log`, `artifacts-sha256.log` and `verification-summary.json` record installation and read-back verification: installed `varus.py` resolves inside scratch `site-packages` and matches source byte-for-byte. The source/installed SHA-256 is `5ec9d1fe91a10c77893a9f9e55d13e407c86d11ab0ff73d49bbec39bd0205aa3`. Built artifacts are also copied to the evidence directory's `dist/`. Pre-fix copies are under `before/`; the focused patch is `inert-fix.diff`.

No commit/push, production user address/settings, Hermes configuration, VPN, Obsidian or personal settings changes. The project directory has no Git repository, so the focused diff is generated against the preserved pre-fix files.

## Final acceptance

Independent read-only re-review passed: no confirmed security concerns or logic blockers. The reviewer directly reproduced template/noscript rejection and executable controls, malformed/missing identities, and duplicate physical-ID/TMS failures. Its fresh run returned 335 tests passed; Ruff and formatting passed. The reviewer also compared the installed wheel's VARUS module to the source bytes.

Parent verification independently returned 335 tests passed, clean Ruff/format checks and successful real VARUS stdio search/card/batch/quantity/unit/error exercises. Raw result: `/home/everchaser/.hermes/cache/scratch/varus-parent-accepted-stdio.json`. Final review delivery is retained in session history; its tool trace is `/home/everchaser/.hermes/cache/delegation/live/deleg_6f8f1054/task-0.log`.

Production location remains unconfigured. Reload the shopping MCP after configuring a verified location. The tested coordinate-to-nearest-shop flow works; a successful live unique text-address geocoding result is not claimed.
