import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from marketplace_mcp.core import MarketplaceService
from marketplace_mcp.server import create_server


async def test_additive_three_tool_schema():
    tools = await create_server(MarketplaceService({})).list_tools()
    assert {t.name for t in tools} == {"search", "get_card", "get_cards"}
    batch = next(t for t in tools if t.name == "get_cards")
    assert "quantities" in batch.inputSchema["properties"]
    assert "units" in batch.inputSchema["properties"]
    assert "quantities" not in batch.inputSchema.get("required", [])


def test_cli_registers_varus_settings_without_relay_proxy(monkeypatch, tmp_path):
    from marketplace_mcp import __main__ as cli

    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"varus": {"shop_id": 1}}))
    seen = []

    class Server:
        def run(self, transport):
            assert transport == "stdio"

    def create(service):
        assert set(service.providers) == {"varus", "yandex_market"}
        v = service.providers["varus"]
        assert v.config == {"shop_id": 1}
        assert not hasattr(v.transport, "proxy")
        seen.append(True)
        return Server()

    monkeypatch.setattr(cli, "create_server", create)
    monkeypatch.setattr(sys, "argv", ["marketplace-mcp", "--settings", str(f)])
    cli.main()
    assert seen


async def test_real_stdio_unconfigured_is_graceful(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "marketplace_mcp", "--settings", str(tmp_path / "missing.json")],
        env=dict(os.environ),
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        r = await session.call_tool(
            "get_card", {"marketplace": "varus", "url": "https://varus.ua/sample-product"}
        )
        assert not r.isError
        assert r.structuredContent["error"]["code"] == "location_unconfigured"
