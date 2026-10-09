"""Exercise the actual stdio server against public Yandex Market pages.

No fixtures, saved search output, or fallback synthetic data are used here.
"""

from __future__ import annotations

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


def tool_payload(result) -> dict:
    if result.isError:
        raise AssertionError(f"MCP protocol/tool error: {result.content}")
    if result.structuredContent is not None:
        return result.structuredContent
    texts = [part.text for part in result.content if hasattr(part, "text")]
    if len(texts) != 1:
        raise AssertionError("Expected one JSON text result or structuredContent")
    return json.loads(texts[0])


async def exercise(args) -> None:
    command_args = ["-m", "marketplace_mcp"]
    if args.nic:
        command_args.extend(["--nic", args.nic])
    record = {"observed_at": datetime.now(UTC).isoformat(), "calls": []}
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    def save() -> None:
        output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    params = StdioServerParameters(
        command=sys.executable,
        args=command_args,
        cwd=str(ROOT),
        env={
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "HOME", "USER", "LANG", "TMPDIR"}
        },
    )
    try:
        async with (
            asyncio.timeout(240),
            stdio_client(params) as (read, write),
            ClientSession(read, write, read_timeout_seconds=None) as session,
        ):
            await session.initialize()
            tools = (await session.list_tools()).tools
            record["tools"] = [t.name for t in tools]
            save()
            assert set(record["tools"]) == {"search", "get_card", "get_cards"}
            assert len(tools) == 3
            assert all(t.annotations and t.annotations.readOnlyHint for t in tools)

            async def call(name: str, arguments: dict) -> dict:
                payload = tool_payload(await session.call_tool(name, arguments))
                record["calls"].append({"tool": name, "arguments": arguments, "response": payload})
                save()
                return payload

            search = await call(
                "search",
                {
                    "marketplace": "yandex_market",
                    "query": "искусственный плющ",
                    "limit": 4,
                },
            )
            assert search["status"] == "ok", search
            assert search["returned"] == len(search["items"])
            urls = []
            for product in search["items"]:
                url = product.get("url")
                if url and "/card/" in url and url not in urls:
                    urls.append(url)
            assert len(urls) >= 2, "Live search did not supply two distinct /card/ links"
            urls = urls[:2]

            single = await call(
                "get_card",
                {
                    "marketplace": "yandex_market",
                    "url": urls[0],
                    "review_limit": 3,
                },
            )
            assert single["status"] == "ok", single
            assert single["product"]["title"]
            assert single["product"]["url"] == urls[0]
            assert single["product"]["reviews_returned"] == len(single["product"]["reviews"])
            assert single["product"]["delivery"] is None

            batch = await call(
                "get_cards",
                {
                    "marketplace": "yandex_market",
                    "urls": urls,
                    "review_limit": 3,
                },
            )
            assert batch["status"] == "ok", batch
            assert batch["requested"] == 2
            assert batch["returned"] == 2 and batch["failed"] == 0
            assert [r["url"] for r in batch["results"]] == urls
            assert all(r["status"] == "ok" and r["product"]["title"] for r in batch["results"])

            mixed = await call(
                "get_cards",
                {
                    "marketplace": "yandex_market",
                    "urls": [urls[0], "https://example.com/product/1", urls[1]],
                    "review_limit": 3,
                },
            )
            assert mixed["status"] == "partial", mixed
            assert mixed["requested"] == 3 and mixed["returned"] == 2 and mixed["failed"] == 1
            assert mixed["results"][1]["status"] == "error"
            assert mixed["results"][1]["error"]["retryable"] is False

            unsupported = await call(
                "search",
                {
                    "marketplace": "not_supported",
                    "query": "искусственный плющ",
                },
            )
            assert unsupported["status"] == "error", unsupported
            assert unsupported["error"]["retryable"] is False

            record["passed"] = True
            save()
            summary = {
                "passed": True,
                "tools": record["tools"],
                "search_returned": search["returned"],
                "batch_returned": batch["returned"],
                "mixed_failed": mixed["failed"],
                "output": str(output),
                "cards": [
                    {
                        "title": r["product"]["title"],
                        "price_rub": r["product"].get("price_rub"),
                        "seller": r["product"].get("seller"),
                    }
                    for r in batch["results"]
                ],
            }
            print(json.dumps(summary, ensure_ascii=False, indent=2))
    except BaseException as exc:
        record["passed"] = False
        record["failure"] = f"{type(exc).__name__}: {exc}"
        save()
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nic", default=None)
    parser.add_argument("--output", required=True)
    asyncio.run(exercise(parser.parse_args()))


if __name__ == "__main__":
    main()
