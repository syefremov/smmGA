"""Safe fake connector used by local development and automated tests."""

from smm_gpt.domain.status import ConnectorStatus, ServiceState


class FakeSocialConnector:
    """Advertise read-only fake mode; no external mutation method exists."""

    async def status(self) -> ConnectorStatus:
        return ConnectorStatus(
            name="fake-social",
            state=ServiceState.READY,
            can_publish=False,
            mode="fake-read-only",
        )
