"""Versioned system status API used by the internal web application."""

from typing import Annotated

from fastapi import APIRouter, Depends

from smm_gpt.api.dependencies import get_system_status_service
from smm_gpt.domain.status import SystemStatus
from smm_gpt.services.system_status import SystemStatusService

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/status", response_model=SystemStatus)
async def system_status(
    service: Annotated[SystemStatusService, Depends(get_system_status_service)],
) -> SystemStatus:
    """Return dependency and connector state without exposing secrets."""

    return await service.read()
