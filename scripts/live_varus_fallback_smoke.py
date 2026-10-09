"""Real read-only stdio fallback checks using bounded observed discovery data.

Never substitute fixtures for upstream products. Missing-primary candidates may
remain unresolved: that is recorded, not advertised as a successful allocation.
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
CONTROL = "https://varus.ua/maslo-zlagoda-solodkovershkove-ekstra-82-180-g"


async def exercise(args):
    discovery = json.loads(Path(args.discovery).read_text())
    candidates = [row for row in discovery["cards"] if row["states"][0] == "unavailable"]
    assert candidates, "Bounded real discovery contained no explicitly unavailable primary product"
    candidate = next((row for row in candidates if "available" in row["states"][1:]), candidates[0])
    missing = candidate["url"]
    record = {
        "observed_at": datetime.now(UTC).isoformat(),
        "sample_location": "Public sample shop; not a user address",
        "candidate_discovery": {"url": missing, "states": candidate["states"]},
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
            batch_schema = next(t for t in tools if t.name == "get_cards").inputSchema
            assert batch_schema["properties"]["max_extra_shops"]["maximum"] == 3

            async def call(arguments, expect_tool_error=False):
                result = await session.call_tool("get_cards", {"marketplace": "varus", **arguments})
                payload = result.structuredContent or {"tool_error": [c.text for c in result.content]}
                record["calls"].append(
                    {"arguments": arguments, "isError": bool(result.isError), "response": payload}
                )
                save()
                assert bool(result.isError) == expect_tool_error, payload
                return payload

            full = await call({"urls": [CONTROL, CONTROL], "quantities": ["2", "3"]})
            assert full["available"] == 2 and len(full["attempted_shops"]) == 1, full
            assert full["estimate"]["ordinary"]["complete"] and full["estimate"]["per_shop"], full

            limits = []
            for limit in range(4):
                result = await call(
                    {
                        "urls": [CONTROL, missing, missing],
                        "quantities": ["1", "2", "3"],
                        "max_extra_shops": limit,
                    }
                )
                limits.append(result)
                attempts = result["attempted_shops"]
                assert len(attempts) <= limit + 1
                assert len({(s["id"], s["tms_id"]) for s in attempts}) == len(attempts)
                assert result["primary_shop"]["id"] == discovery["primary"]["id"]
                assert result["results"][0]["shop"]["id"] == result["primary_shop"]["id"]
                assert result["results"][0]["product"]["availability"]["state"] == "available"
                assert (
                    result["results"][1]["primary_observation"]["product"]["availability"]["state"]
                    == "unavailable"
                ), result
                assert [r["url"] for r in result["results"]] == [CONTROL, missing, missing]
                assert result["requested"] == result["returned"] == 3 and result["failed"] == 0
                assert all(s["checked_indices"] == [1, 2] for s in attempts[1:])
                expected_ids = [discovery["primary"]["id"], *[s["id"] for s in discovery["alternatives"]]][
                    : len(attempts)
                ]
                assert [s["id"] for s in attempts] == expected_ids
                if result["available"] == 1:
                    assert len(attempts) == min(1 + limit, 1 + len(discovery["alternatives"]))
                    assert result["estimate"]["ordinary"]["included"] == 1
                else:
                    assert result["available"] == 3
                    assert result["results"][1]["shop"] == result["results"][2]["shop"]

            final = await call({"urls": [CONTROL, missing], "quantities": ["1", "1"]})
            assert len(final["attempted_shops"]) == len(limits[3]["attempted_shops"])
            record["live_fill_observed"] = final["available"] == 2
            record["inspected_shop_ids"] = [s["id"] for s in final["attempted_shops"]]

            partial = await call(
                {"urls": [CONTROL, "https://example.org/private", missing], "quantities": ["1"] * 3}
            )
            assert partial["returned"] == 2 and partial["failed"] == 1 and partial["status"] == "partial"
            assert partial["results"][1]["error"]["code"] == "validation_error"
            assert partial["estimate"]["ordinary"]["complete"] is False
            malformed = await call({"urls": [CONTROL], "quantities": ["not-a-number"]})
            assert malformed["error"]["code"] == "validation_error"
            for invalid in [True, "1", -1, 4]:
                await call({"urls": [CONTROL], "max_extra_shops": invalid}, expect_tool_error=True)
            record["passed"] = True
            save()
            print(json.dumps({k: v for k, v in record.items() if k != "calls"}, ensure_ascii=False, indent=2))
    except BaseException as exc:
        record["passed"] = False
        record["failure"] = str(exc)
        save()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--discovery", required=True, help="Observed adapter discovery.json, never fixtures")
    parser.add_argument("--output", required=True)
    parser.add_argument("--settings", default=str(ROOT / "examples/shopping.sample.json"))
    parser.add_argument("--python", help="Separate clean-wheel interpreter")
    parser.add_argument("--nic", help="Existing Yandex-only relay; VARUS remains host-route")
    asyncio.run(exercise(parser.parse_args()))


if __name__ == "__main__":
    main()
