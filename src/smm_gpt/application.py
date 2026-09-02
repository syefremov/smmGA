"""ASGI application factory combining REST and MCP transports."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy.exc import SQLAlchemyError

from smm_gpt import __version__
from smm_gpt.api.routes import health, identity, operations, system
from smm_gpt.core.config import Settings, get_settings
from smm_gpt.core.request_context import request_context, request_id
from smm_gpt.domain.access import AccessDenied, Conflict
from smm_gpt.domain.operations import OperationError
from smm_gpt.infrastructure.database import Database
from smm_gpt.infrastructure.redis import RedisCache
from smm_gpt.integrations.base import ConnectorRegistry
from smm_gpt.integrations.fake import FakeSocialConnector
from smm_gpt.mcp.auth import MCPVerifier
from smm_gpt.mcp.server import create_mcp_server
from smm_gpt.services.access import AccessService
from smm_gpt.services.oidc import OIDCClient
from smm_gpt.services.sessions import SessionService
from smm_gpt.services.system_status import SystemStatusService


def build_status_service(settings: Settings) -> SystemStatusService:
    database = Database(
        settings.database_url.get_secret_value(), settings.dependency_timeout_seconds
    )
    redis = RedisCache(settings.redis_url.get_secret_value(), settings.dependency_timeout_seconds)
    connectors = ConnectorRegistry((FakeSocialConnector(),))
    return SystemStatusService(settings, (database, redis), connectors)


def create_app(
    status_service: SystemStatusService | None = None,
    *,
    settings: Settings | None = None,
    sessions: SessionService | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    service = status_service or build_status_service(settings)
    if settings.auth_enabled and sessions is None:
        database = Database(
            settings.database_url.get_secret_value(), settings.dependency_timeout_seconds
        )
        sessions = SessionService(settings, AccessService(database), OIDCClient(settings))
    verifier = MCPVerifier(sessions.oidc, sessions.access) if sessions else None
    mcp_server = create_mcp_server(service, verifier)
    mcp_app = mcp_server.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=[urlsplit(settings.web_origin).netloc],
            allowed_origins=[settings.web_origin],
        )
        if settings.auth_enabled
        else None,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            if sessions:
                await sessions.access.database.require_restricted_role()
            async with mcp_server.session_manager.run():
                yield
        finally:
            await service.close()
            if sessions:
                await sessions.oidc.close()
                await sessions.access.database.close()

    app = FastAPI(
        title="SMM GPT API",
        summary="Shared API for chat-first SMM operations.",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.system_status_service = service
    app.state.sessions = sessions
    app.include_router(identity.router, prefix="/api/v1")
    app.include_router(operations.router, prefix="/api/v1")

    @app.middleware("http")
    async def privacy_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        correlation_id = uuid4()
        context_token = request_context.set(correlation_id)
        try:
            response = await call_next(request)
            if sessions and response.status_code in {401, 403, 422}:
                await sessions.access.record_denial(None, correlation_id, "http.request_rejected")
            response.headers["X-Request-ID"] = str(correlation_id)
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        finally:
            request_context.reset(context_token)

    def error(code: str, status: int) -> JSONResponse:
        # Keep phase-4 `detail` for existing clients while exposing the shared envelope.
        return JSONResponse(
            {"detail": code, "error": {"code": code, "correlation_id": str(request_id())}},
            status_code=status,
        )

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        return error(str(exc.detail), exc.status_code)

    @app.exception_handler(OperationError)
    async def operation_error(_: Request, exc: OperationError) -> JSONResponse:
        return error(exc.code, exc.status)

    @app.exception_handler(AccessDenied)
    async def access_denied(_: Request, __: AccessDenied) -> JSONResponse:
        return error("access_denied", 403)

    @app.exception_handler(Conflict)
    async def conflict(_: Request, __: Conflict) -> JSONResponse:
        return error("idempotency_conflict", 409)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, __: RequestValidationError) -> JSONResponse:
        # Pydantic's default response echoes submitted values, including unknown secret fields.
        return error("invalid_request", 422)

    @app.exception_handler(SQLAlchemyError)
    async def persistence_unavailable(_: Request, __: SQLAlchemyError) -> JSONResponse:
        return error("service_unavailable", 503)

    if settings.auth_enabled:

        @app.get("/.well-known/oauth-protected-resource/mcp/")
        @app.get("/.well-known/oauth-protected-resource")
        async def protected_resource() -> dict[str, object]:
            return {
                "resource": settings.mcp_resource_url,
                "authorization_servers": [settings.mcp_issuer_url],
                "scopes_supported": ["smm:access"],
            }

    app.include_router(health.router)
    app.include_router(system.router, prefix="/api/v1")
    app.mount("/mcp", mcp_app, name="mcp")
    return app


app = create_app()
