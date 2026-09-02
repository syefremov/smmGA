from mcp import Client

from smm_gpt.core.config import Settings
from smm_gpt.integrations.base import ConnectorRegistry
from smm_gpt.integrations.fake import FakeSocialConnector
from smm_gpt.mcp.server import create_mcp_server
from smm_gpt.services.system_status import SystemStatusService

from ..fakes import FakeProbe


async def test_mcp_capabilities_and_system_status_tool() -> None:
    service = SystemStatusService(
        Settings(env="test"),
        (FakeProbe("postgresql"), FakeProbe("redis")),
        ConnectorRegistry((FakeSocialConnector(),)),
    )
    server = create_mcp_server(service)

    async with Client(server) as client:
        tools = await client.list_tools()
        result = await client.call_tool("system_status", {})

    assert [tool.name for tool in tools.tools] == ["system_status"]
    assert tools.tools[0].annotations is not None
    assert tools.tools[0].annotations.read_only_hint is True
    assert result.structured_content is not None
    assert result.structured_content["state"] == "ready"
