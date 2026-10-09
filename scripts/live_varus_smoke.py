"""Real stdio read-only VARUS verification. No fixtures/fallbacks.

Use only sample public-shop settings, never private/home geocoding addresses.
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_CARD = "https://varus.ua/maslo-zlagoda-solodkovershkove-ekstra-82-180-g"


async def exercise(args):
    record = {
        "sample_location": "Public VARUS shop, Панікахи 15 Дніпро; not a user address",
        "observed_at": datetime.now(UTC).isoformat(),
        "calls": [],
    }
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        output.write_text(json.dumps(record, ensure_ascii=False, indent=2))

    command_args = ["-m", "marketplace_mcp", "--settings", str(Path(args.settings).resolve())]
    if args.nic:
        command_args += ["--nic", args.nic]
    params = StdioServerParameters(
        command=args.python or sys.executable,
        args=command_args,
        cwd=str(ROOT),
        env={
            k: v
            for k, v in os.environ.items()
            if k in {"PATH", "HOME", "USER", "LANG", "TMPDIR", "XDG_CACHE_HOME"}
        },
    )
    try:
        async with (
            asyncio.timeout(300),
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            tools = (await session.list_tools()).tools
            record["tools"] = [t.name for t in tools]
            assert len(tools) == 3 and set(record["tools"]) == {"search", "get_card", "get_cards"}

            async def call(name, arguments):
                r = await session.call_tool(name, arguments)
                assert not r.isError, r
                payload = r.structuredContent or json.loads(r.content[0].text)
                record["calls"].append({"tool": name, "arguments": arguments, "response": payload})
                save()
                return payload

            search = await call("search", {"marketplace": "varus", "query": "молоко", "limit": 2})
            assert search["status"] == "ok" and search["returned"] == len(search["items"]) == 2, search
            assert all(
                x["url_provenance"] == "observed_card_canonical_and_ssr_identity" for x in search["items"]
            )
            urls = [x["url"] for x in search["items"]]
            single = await call(
                "get_card", {"marketplace": "varus", "url": SAMPLE_CARD, "include_reviews": False}
            )
            assert single["status"] == "ok", single
            assert single["shop"]["tms_id"] == "1001", single
            assert single["product"]["sku"] == "2656669"
            assert single["product"]["availability"]["exact_quantity"] is None
            assert single["product"]["availability"]["state"] == "available", single
            batch = await call(
                "get_cards",
                {
                    "marketplace": "varus",
                    "urls": [SAMPLE_CARD, SAMPLE_CARD],
                    "quantities": ["2", "3"],
                    "units": ["unit", "unit"],
                },
            )
            assert batch["status"] == "ok" and batch["returned"] == 2, batch
            ordinary = Decimal(single["product"]["prices"]["ordinary"]) * 5
            assert batch["estimate"]["ordinary"]["total"] == str(
                ordinary.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            ), batch
            assert batch["estimate"]["ordinary"]["complete"] is True
            mixed = await call(
                "get_cards",
                {
                    "marketplace": "varus",
                    "urls": [SAMPLE_CARD, "https://example.org/private", urls[0]],
                    "quantities": ["1", "1", "1"],
                },
            )
            assert mixed["status"] == "partial" and mixed["failed"] == 1 and mixed["returned"] == 2, mixed
            assert mixed["estimate"]["ordinary"]["complete"] is False
            unsupported = await call("search", {"marketplace": "not_supported", "query": "sample"})
            assert unsupported["error"]["code"] == "unsupported_marketplace"
            mismatch = await call(
                "get_cards",
                {"marketplace": "varus", "urls": [SAMPLE_CARD], "quantities": ["0.5"], "units": ["kg"]},
            )
            assert mismatch["estimate"]["ordinary"]["included"] == 0
            assert mismatch["estimate"]["lines"][0]["excluded_reason"] == "unit_mismatch"
            weighted_url = (
                "https://varus.ua/banan-vag"  # observed catalogue route, independently SSR-verified
            )
            weighted = await call("get_card", {"marketplace": "varus", "url": weighted_url})
            assert weighted["status"] == "ok" and weighted["product"]["prices"]["basis"] == "kg", weighted
            assert weighted["product"]["prices"]["promotional"] is None, weighted
            weight_batch = await call(
                "get_cards",
                {"marketplace": "varus", "urls": [weighted_url], "quantities": ["0.5"], "units": ["kg"]},
            )
            assert weight_batch["status"] == "ok", weight_batch
            if weighted["product"]["availability"]["state"] == "available":
                weight_total = Decimal(weighted["product"]["prices"]["ordinary"]) * Decimal("0.5")
                assert weight_batch["estimate"]["ordinary"]["total"] == str(
                    weight_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                )
            else:
                assert weight_batch["estimate"]["ordinary"]["included"] == 0
            assert weight_batch["estimate"]["promotional"]["included"] == 0
            record["passed"] = True
            save()
            print(
                json.dumps(
                    {
                        "passed": True,
                        "output": str(output),
                        "shop": single["shop"],
                        "prices": single["product"]["prices"],
                        "ordinary_total": batch["estimate"]["ordinary"],
                        "promotional_total": batch["estimate"]["promotional"],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
    except BaseException as exc:
        record["passed"] = False
        record["failure"] = str(exc)
        save()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--settings", default=str(ROOT / "examples/shopping.sample.json"))
    parser.add_argument("--python", help="Separate clean-wheel interpreter")
    parser.add_argument("--nic", help="Optional existing Yandex-only relay; VARUS stays on host route")
    asyncio.run(exercise(parser.parse_args()))


if __name__ == "__main__":
    main()
