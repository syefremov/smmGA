"""Contracts implemented by every social platform connector."""

from typing import Protocol

from smm_gpt.domain.status import ConnectorStatus


class SocialConnector(Protocol):
    """Small capability surface available before publishing is implemented."""

    async def status(self) -> ConnectorStatus:
        """Return connector health and explicit write capability."""


class ConnectorRegistry:
    """Resolve configured connectors without exposing implementation details."""

    def __init__(self, connectors: tuple[SocialConnector, ...]) -> None:
        self._connectors = connectors

    async def statuses(self) -> tuple[ConnectorStatus, ...]:
        return tuple([await connector.status() for connector in self._connectors])
