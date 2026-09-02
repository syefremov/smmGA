"""Thin MCP tools over shared application services."""

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from smm_gpt import __version__
from smm_gpt.services.system_status import SystemStatusService

SERVER_INSTRUCTIONS = (
    "SMM GPT is a private chat-first operations system. This phase exposes only read-only "
    "system status. Never claim that content was approved, scheduled, or published; no external "
    "social-network writes are available. Return concise status and surface unavailable services."
)


def create_mcp_server(status_service: SystemStatusService) -> MCPServer:
    """Build the MCP server with explicitly safe phase-two capabilities."""

    server = MCPServer(
        name="smm-gpt",
        title="SMM GPT",
        description="Private tools for the shared SMM operations system.",
        instructions=SERVER_INSTRUCTIONS,
        version=__version__,
    )

    @server.tool(
        name="system_status",
        description="Read API, database, queue and connector readiness.",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    async def system_status() -> dict[str, object]:
        result = await status_service.read()
        return result.model_dump(mode="json")

    return server
