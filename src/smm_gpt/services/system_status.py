"""Dependency-aware system status application service."""

import asyncio
from collections.abc import Awaitable
from time import perf_counter
from typing import Protocol

from smm_gpt import __version__
from smm_gpt.core.config import Settings
from smm_gpt.domain.status import DependencyStatus, ServiceState, SystemState, SystemStatus
from smm_gpt.integrations.base import ConnectorRegistry


class DependencyProbe(Protocol):
    name: str

    def ping(self) -> Awaitable[None]: ...

    def close(self) -> Awaitable[None]: ...


class SystemStatusService:
    """Collect health without leaking exception details or credentials."""

    def __init__(
        self,
        settings: Settings,
        probes: tuple[DependencyProbe, ...],
        connectors: ConnectorRegistry,
    ) -> None:
        self._settings = settings
        self._probes = probes
        self._connectors = connectors

    async def read(self) -> SystemStatus:
        dependencies = tuple(await asyncio.gather(*(self._probe(item) for item in self._probes)))
        state = (
            SystemState.READY
            if all(item.state is ServiceState.READY for item in dependencies)
            else SystemState.DEGRADED
        )
        return SystemStatus(
            application="smm-gpt",
            version=__version__,
            environment=self._settings.env,
            state=state,
            dependencies=dependencies,
            connectors=await self._connectors.statuses(),
        )

    async def close(self) -> None:
        await asyncio.gather(*(probe.close() for probe in self._probes))

    async def _probe(self, probe: DependencyProbe) -> DependencyStatus:
        started = perf_counter()
        try:
            await probe.ping()
            state = ServiceState.READY
        except Exception:  # Readiness is a trust boundary; adapter errors are never returned.
            state = ServiceState.UNAVAILABLE
        return DependencyStatus(
            name=probe.name,
            state=state,
            latency_ms=round((perf_counter() - started) * 1000, 2),
        )
