import importlib
import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.asyncio
async def test_exact_three_tools_and_readonly_annotations():
    from marketplace_mcp.core import MarketplaceService

    server = importlib.import_module("marketplace_mcp.server").create_server(MarketplaceService({}))
    tools = await server.list_tools()
    assert {t.name for t in tools} == {"search", "get_card", "get_cards"}
    for tool in tools:
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.idempotentHint is True
        assert "marketplace" in tool.inputSchema["properties"]
        assert tool.outputSchema is not None


@pytest.mark.asyncio
async def test_actual_stdio_initialize_list_and_structured_mocked_output():
    # A test-only harness injects deterministic HTML via HTTPX transport.
    params = StdioServerParameters(
        command=sys.executable, args=[str(Path(__file__).with_name("stdio_harness.py"))], env=dict(os.environ)
    )
    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
        await session.initialize()
        assert {t.name for t in (await session.list_tools()).tools} == {"search", "get_card", "get_cards"}
        result = await session.call_tool("get_card", {"url": "https://market.yandex.ru/card/test/123"})
        assert not result.isError
        data = result.structuredContent
        assert data["status"] == "ok" and data["product"]["title"] == "Synthetic test only"
        assert json.loads(result.content[0].text) == data
        bad = await session.call_tool("search", {"query": "test", "marketplace": "ozon"})
        assert bad.structuredContent["error"]["code"] == "unsupported_marketplace"


def test_module_help_and_cli_entrypoint():
    import subprocess

    result = subprocess.run(
        [sys.executable, "-m", "marketplace_mcp", "--help"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0 and "--nic" in result.stdout
    assert "stdio" in result.stdout


def test_cli_invalid_nic_fails_without_stdout():
    import subprocess

    result = subprocess.run(
        [sys.executable, "-m", "marketplace_mcp", "--nic", "missing-mcp-nic"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert "NIC relay" in result.stderr


def test_cli_uses_relay_and_always_closes_it(monkeypatch):
    from marketplace_mcp import __main__ as cli

    events = []

    class Relay:
        proxy_url = "http://127.0.0.1:12345"

        def __init__(self, nic):
            assert nic == "enp9s0"

        def __enter__(self):
            events.append("start")
            return self

        def __exit__(self, *args):
            events.append("close")

    class Server:
        def run(self, transport):
            assert transport == "stdio"
            events.append("run")

    monkeypatch.setattr(cli, "YandexRelay", Relay)
    monkeypatch.setattr(cli, "create_server", lambda service: Server())

    def make_transport(**kwargs):
        assert kwargs["proxy"] == Relay.proxy_url
        return object()

    monkeypatch.setattr(cli, "SafeTransport", make_transport)
    monkeypatch.setattr(sys, "argv", ["marketplace-mcp", "--nic", "enp9s0"])
    cli.main()
    assert events == ["start", "run", "close"]
