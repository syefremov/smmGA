"""Thin MCP tools over shared application services."""

from uuid import UUID

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl

from smm_gpt import __version__
from smm_gpt.core.request_context import request_id
from smm_gpt.domain.access import AccessDenied, Principal
from smm_gpt.mcp.auth import MCPVerifier
from smm_gpt.services.system_status import SystemStatusService

SERVER_INSTRUCTIONS = (
    "SMM GPT is a private chat-first operations system. Available capabilities are system status, "
    "authorized workspace reads and durable diagnostic jobs. Never claim that content was "
    "approved, scheduled, or published; no external "
    "social-network writes are available. Return concise status and surface unavailable services."
)


def create_mcp_server(
    status_service: SystemStatusService, verifier: MCPVerifier | None = None
) -> MCPServer:
    """Build thin tools, enabling tenant capabilities only with personal authentication."""

    server = MCPServer(
        name="smm-gpt",
        title="SMM GPT",
        description="Private tools for the shared SMM operations system.",
        instructions=SERVER_INSTRUCTIONS,
        version=__version__,
        token_verifier=verifier,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(verifier.oidc.settings.mcp_issuer_url),
            resource_server_url=AnyHttpUrl(verifier.oidc.settings.mcp_resource_url),
            required_scopes=["smm:access"],
        )
        if verifier
        else None,
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
        if verifier:
            await current_principal()
        result = await status_service.read()
        return result.model_dump(mode="json")

    if verifier is not None:

        async def current_principal() -> Principal:
            token = get_access_token()
            if token is None:
                raise AccessDenied("authentication_required")
            # Revalidate on each tool invocation, including in-process transports/batches.
            verified = await verifier.verify_token(token.token)
            if verified is None or verified.subject is None:
                raise AccessDenied("authentication_required")
            return await verifier.access.identity(
                verifier.oidc.settings.mcp_issuer_url,
                verified.subject,
                (verified.claims or {}).get("mfa") is True,
            )

        @server.tool(
            name="workspace_read",
            annotations=ToolAnnotations(
                readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
            ),
        )
        async def workspace_read(workspace_id: UUID) -> dict[str, str]:
            return await verifier.access.workspace(
                await current_principal(), workspace_id, request_id()
            )

        @server.tool(
            name="diagnostic_job_create",
            annotations=ToolAnnotations(
                readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
            ),
        )
        async def diagnostic_job_create(workspace_id: UUID, idempotency_key: str) -> dict[str, str]:
            job = await verifier.access.create_job(
                await current_principal(), workspace_id, idempotency_key, request_id()
            )
            return {"job_id": str(job)}

    return server
