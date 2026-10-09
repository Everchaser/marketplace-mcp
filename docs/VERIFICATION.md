# MVP verification

Artifact: `/home/everchaser/Projects/marketplace-mcp`, version `0.1.0`.

## Automated verification

Parent execution after the final provenance fix:

```text
uv sync --locked --python 3.12
Resolved 41 packages; checked 39 packages

uv run --locked pytest -q
154 passed in 3.54s

uv run --locked ruff check . --output-format concise
All checks passed!

uv run --locked ruff format --check .
25 files already formatted

uv build
Successfully built dist/marketplace_mcp-0.1.0.tar.gz
Successfully built dist/marketplace_mcp-0.1.0-py3-none-any.whl
```

An independent final reviewer reran the 154 tests, lint, formatting, prior error reproductions and official-SDK stdio discovery. Final verdict: passed, no blocking security concerns or logical errors. Mixed-quote source snippets no longer cross-assign URLs; LDJSON IDs exclude URL query parameters. Same-SKU distinct offers remain separate. Separate RED/GREEN evidence records are in this directory.

## Actual public Yandex checks

The parent ran `scripts/live_smoke.py --nic enp9s0` through real MCP stdio on the final source installation and on the rebuilt wheel in a separate environment with no old-project package dependency.

Both runs passed:

- Exactly three discovered read-only tools: `search`, `get_card`, `get_cards`.
- Search returned 4 actual products; declared count matched the list length.
- One real `/card/` URL returned a product and a correctly counted review subset.
- A two-card batch returned both in input order.
- A mixed batch with two real URLs and one rejected external-domain URL returned `partial`, 3 requested, 2 returned, 1 failed.
- Unsupported marketplace returned a structured non-retryable error.
- Personal delivery remained explicitly unknown, not invented.

Raw result files (local scratch, not bundled with the source):

- `/home/everchaser/.hermes/cache/scratch/marketplace-mcp-live-release.json`
- `/home/everchaser/.hermes/cache/scratch/marketplace-mcp-wheel-live-release.json`

These are observation snapshots, not future price guarantees. The test does not imply every marketplace product or future page format will parse correctly.

## Packaging and unchanged external state

The parent checked the wheel's vendor MIT license and source archive contents; no `.venv` or `.env` was bundled. Runtime dependencies are HTTPX, the official MCP SDK and Pydantic; the old MCP aggregator is not imported. The vendored upstream parser is unchanged and its notice is retained.

The current Hermes configuration still has the old `marketplaces` server enabled and no `marketplace_local` entry. No switch, live configuration write, gateway restart, host VPN change, old-project edit, commit or push was performed. Follow README instructions to connect the new MCP and disable the old one with rollback available.

Not verified: reboot behavior with the new entry, personal address/PVZ delivery dates, authenticated accounts, additional marketplaces. Finite TCP connect waits can take up to the configured connection deadline during shutdown; stalled DNS no longer waits indefinitely.
