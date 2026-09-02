"""Do not echo malformed arguments, exception messages or SQL through the SDK."""

from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from mcp.types import CallToolResult, InputRequiredResult, TextContent
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from smm_gpt.core.request_context import request_id
from smm_gpt.domain.access import AccessDenied, Conflict
from smm_gpt.domain.operations import OperationError


class PrivateMCPServer(MCPServer):
    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Context[Any, Any] | None = None
    ) -> CallToolResult | InputRequiredResult:
        try:
            return await super().call_tool(name, arguments, context)
        except Exception as exc:
            cause: BaseException | None = exc
            code = "service_unavailable"
            while cause:
                if isinstance(cause, OperationError):
                    code = cause.code
                    break
                if isinstance(cause, AccessDenied):
                    code = "access_denied"
                    break
                if isinstance(cause, Conflict):
                    code = "idempotency_conflict"
                    break
                if isinstance(cause, ValidationError) or (
                    isinstance(cause, ToolError) and not isinstance(cause, UnexpectedToolError)
                ):
                    code = "invalid_request"
                if isinstance(cause, SQLAlchemyError):
                    code = "service_unavailable"
                    break
                cause = cause.__cause__
            return CallToolResult(
                is_error=True,
                content=[TextContent(type="text", text=code)],
                structured_content={"error": {"code": code, "correlation_id": str(request_id())}},
            )
