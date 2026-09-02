"""Container liveness and dependency readiness endpoints."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, ConfigDict

from smm_gpt.api.dependencies import get_system_status_service
from smm_gpt.domain.status import SystemState, SystemStatus
from smm_gpt.services.system_status import SystemStatusService

router = APIRouter(prefix="/health", tags=["health"])


class LiveResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ok"] = "ok"


@router.get("/live", response_model=LiveResponse)
async def live() -> LiveResponse:
    """Confirm that the ASGI process can serve requests."""

    return LiveResponse()


@router.get(
    "/ready",
    response_model=SystemStatus,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": SystemStatus}},
)
async def ready(
    response: Response,
    service: Annotated[SystemStatusService, Depends(get_system_status_service)],
) -> SystemStatus:
    """Report ready only when PostgreSQL and Redis respond."""

    result = await service.read()
    if result.state is not SystemState.READY:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result
