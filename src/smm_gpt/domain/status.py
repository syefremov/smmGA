"""System status models shared by HTTP and MCP transports."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ServiceState(StrEnum):
    READY = "ready"
    UNAVAILABLE = "unavailable"


class SystemState(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"


class DependencyStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    state: ServiceState
    latency_ms: float = Field(ge=0)


class ConnectorStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    state: ServiceState
    can_publish: bool
    mode: str


class SystemStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    application: str
    version: str
    environment: str
    state: SystemState
    dependencies: tuple[DependencyStatus, ...]
    connectors: tuple[ConnectorStatus, ...]
