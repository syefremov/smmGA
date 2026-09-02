import pytest

from smm_gpt.core.config import Settings
from smm_gpt.workers.ai import poll


async def test_disabled_worker_does_not_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected(*args: object) -> None:
        pytest.fail("Disabled queue must not open a database or provider")

    monkeypatch.setattr("smm_gpt.workers.ai.Database", unexpected)
    assert await poll(Settings(_env_file=None)) == 0
