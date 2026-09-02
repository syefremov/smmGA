from smm_gpt.domain.status import ServiceState
from smm_gpt.integrations.fake import FakeSocialConnector


async def test_fake_connector_is_read_only() -> None:
    status = await FakeSocialConnector().status()

    assert status.state is ServiceState.READY
    assert status.mode == "fake-read-only"
    assert status.can_publish is False
