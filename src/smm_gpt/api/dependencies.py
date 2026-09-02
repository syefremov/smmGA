"""FastAPI dependency accessors."""

from typing import cast

from fastapi import Request

from smm_gpt.services.system_status import SystemStatusService


def get_system_status_service(request: Request) -> SystemStatusService:
    return cast(SystemStatusService, request.app.state.system_status_service)
