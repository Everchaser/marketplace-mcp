# VARUS missing-item batch fallback — verification

This records executed checks, not independent review approval. Evidence directory:
`/home/everchaser/.hermes/cache/scratch/varus-nearby-fallback`.

## Implemented behavior

- Exactly search/get_card/get_cards. Only VARUS batch adds missing-item fallback, default primary plus up to THREE extras globally; additive strict `max_extra_shops=0..3`. Single card and Yandex behavior unchanged.
- Preserve explicit primary, rank eligible unique physical shops by unrounded distance from the original coordinate/geocoded origin. Shop-ID override without coordinates anchors alternatives to primary-shop coordinates; with coordinates retains their original anchor. Address remains ignored with shop-ID override, as before.
- One verified SSR document per distinct exact URL and one catalogue per batch. No per-shop fetching, URL/SKU substitution, relocation of available lines, splitting quantity or bypass of transport protections. Unknown/missing/ambiguous inventory entries are unknown, not absence. Invalid URL/SSR/network failures retain original errors and never enter alternative checks.
- Actual inspections, including unknown/no-match points, consume the shared cap. Return primary shop, attempted shops/distances/checked indices, origin/stop reason, distinct availability count, unresolved original indices/reasons, primary observation and per-line attempt evidence, confirmed allocations and chosen-shop prices. Returned/failed remain card-reading counts.
- Explicit internal immutable allocation authorization is validated against all available result indices and physical/TMS identities. Default estimates still fail closed for mixed shops; authorized VARUS batches add per-shop subtotals and grand partial totals with separate ordinary/promotional coverage. No ordinary-price promo fallback, fees, reservations or requested-quantity guarantee.
- Inert HTML SSR context safeguards and all transport/cache/timeouts/pacing/caps remain unchanged.

## Vertical TDD and regression checks

Snapshot excluding caches/.venv/dist: `before/`. Initial live source baseline was **335 passed in 6.74s**; independent snapshot rerun `baseline.log`: **335 passed in 6.91s**.

| Slice | RED before implementation | GREEN full suite |
| --- | --- | --- |
| Primary early stop, duplicate reuse, strict limit | `01-red.log`: 8 failures | `01-green-full.log`: 343 passed |
| Second-shop exact-product fill / primary evidence | `02-red.log`: 1 failure | `02-green-full.log`: 344 passed |
| Original URL errors survive catalogue failure; no invalid-only networking | `03-red.log`: 2 failures | `03-green-full.log`: 346 passed |
| Explicit multishop allocations / Decimal subtotal authorization | `04-red.log`: 1 failure | `04-green-full.log`: 347 passed |
| Global cap / actual attempt metadata including no-match point | `05-red.log`: 4 failures | `05-green-full.log`: 351 passed |
| Override plus coordinates preserves original origin | `06-red.log`: 1 failure and 1 passing control | `06-green-full.log`: 396 passed |

Additional controls cover second/third/fourth fill and fifth-shop exclusion, all-four unresolved, catalogue exhaustion, duplicates/order, inactive/service/invalid-coordinate and ambiguous physical/TMS points, original unknown/captcha/network/SSR errors, price/unit exclusions and no movement for promo, accidental mixed-estimate rejection and strict MCP schema validation. `controls.log` preserves the initial origin bug and one fixture argument error; the fixture was corrected before the focused origin RED. No production change was made to satisfy an invalid fixture.

Final source checks: `pytest-final.log`: **398 passed in 7.04s** (including two additional passing controls for simultaneous third/fourth fills and one-time public-origin geocoding); `ruff.log`: **All checks passed**; `format.log`: **44 files already formatted**; `build.log`: successful wheel and sdist. Dependencies exported from the locked project in `requirements.txt`, installed non-editably into new CPython 3.12 scratch `wheel-env`; `clean-env.log` / `wheel-install.log` record the installation. `wheel-provenance.log` checks actual installed paths and byte hashes for all changed Python modules. `fallback.diff` compares exact changed project files against the snapshot; `verification-summary.json` aggregates read-back assertions. Built artifacts and hashes are under `dist/` and `artifacts-sha256.log` in the evidence directory.

## Real upstream evidence — no invented successful fill

Bounded adapter-only discovery (`discover.py`, `discovery.json`, `discovery.log`) checked six search queries, limit two each: 12 real canonical/SSR-verified cards. **No explicitly unavailable primary / available-nearby candidate was observed among those 12 products.** Several nearby entries were unknown; unknown was not called absent. This is a bounded observation, not a claim that no such product exists.

Observed missing candidate: `https://varus.ua/chipsi-kozacki-z-chasnikom-riga-hlib-110g`. Source and clean-wheel real stdio fallback smoke both found it explicitly unavailable in the four inspected points:

1. physical `1`, TMS `1001`, sample public Panikakhi 15 shop, distance 0;
2. physical `139247`, TMS `1117`, distance 2.960 km;
3. physical `139231`, TMS `1055`, distance 2.998 km;
4. physical `139230`, TMS `1152`, distance 3.006 km.

`source-fallback.json/.log` and `wheel-fallback.json/.log` verify real limits 0/1/2/3 produce actual 1/2/3/4 inspected shops, global duplicate indices, no more than four, preserved available butter line, explicit unresolved missing lines, partial/malformed quantity and strict invalid-limit controls. Each discovers exactly three tools. `live_fill_observed=false` is explicitly recorded; **live successful multishop allocation is not claimed**. Synthetic tests verify successful second/third/fourth-shop allocations and their totals.

Unmodified existing VARUS regression smoke passed against source and wheel: `source-varus.json/.log`, `wheel-varus.json/.log`. Actual ordinary control SKU `2656669` at shop 1: ordinary 124 UAH / promotional 74.9 UAH; quantities 2+3 produced 620.00 / 374.50. Source and wheel each verify search, single/batch, mixed error, quantity/unit mismatch and kg no-promotion sentinel. These observations are not purchases or reservations.

## Blocked / unproven checks

The tool approval layer denied the requested existing Yandex live smoke and clean-wheel full pytest command; neither ran, and neither was retried or bypassed. Thus Yandex **live** regression and clean-wheel **full-suite** verification remain pending parent/user authorization. Source automated Yandex regressions passed within the final 398-test suite. Clean-wheel VARUS and fallback live stdio exercises did run and pass; installed changed-module byte provenance is checked separately. See `blocked-checks.txt`.

## Final source acceptance

Independent read-only review passed with no confirmed security or logic blockers. The reviewer verified the exact snapshot diff, independently ran 398 source tests, Ruff and formatting, and checked 200 additional synthetic cases against an independently computed ordering/allocation/Decimal oracle. The denied Yandex live and clean-wheel full-suite checks were not run or bypassed and remain pending authorization.

Parent verification independently returned 398 tests passed, clean Ruff/format checks, and a successful live fallback stdio exercise. Raw result: `/home/everchaser/.hermes/cache/scratch/varus-parent-fallback-stdio.json`. Limits 0..3 obeyed the global cap; the observed missing item stayed unavailable in all four inspected shops. Successful live multishop fill remains unproven, not claimed. Review delivery is retained in session history and `/home/everchaser/.hermes/cache/delegation/live/deleg_7619fc6c/task-0.log`.

No Git metadata exists, commit/push was not performed. No production Hermes configuration, private address/settings, VPN/DNS, Obsidian or orders were changed. Only the repository and requested scratch evidence were written. The public sample coordinates were never stored as user settings.
