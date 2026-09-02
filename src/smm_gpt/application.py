"""ASGI application factory combining REST and MCP transports."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from smm_gpt import __version__
from smm_gpt.api.routes import health, system
from smm_gpt.core.config import Settings, get_settings
from smm_gpt.infrastructure.database import Database
from smm_gpt.infrastructure.redis import RedisCache
from smm_gpt.integrations.base import ConnectorRegistry
from smm_gpt.integrations.fake import FakeSocialConnector
from smm_gpt.mcp.server import create_mcp_server
from smm_gpt.services.system_status import SystemStatusService


def build_status_service(settings: Settings) -> SystemStatusService:
    database = Database(
        settings.database_url.get_secret_value(), settings.dependency_timeout_seconds
    )
    redis = RedisCache(settings.redis_url.get_secret_value(), settings.dependency_timeout_seconds)
    connectors = ConnectorRegistry((FakeSocialConnector(),))
    return SystemStatusService(settings, (database, redis), connectors)


def create_app(status_service: SystemStatusService | None = None) -> FastAPI:
    settings = get_settings()
    service = status_service or build_status_service(settings)
    mcp_server = create_mcp_server(service)
    mcp_app = mcp_server.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            async with mcp_server.session_manager.run():
                yield
        finally:
            await service.close()

    app = FastAPI(
        title="SMM GPT API",
        summary="Shared API for chat-first SMM operations.",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.system_status_service = service
    app.include_router(health.router)
    app.include_router(system.router, prefix="/api/v1")
    app.mount("/mcp", mcp_app, name="mcp")
    return app


app = create_app()
