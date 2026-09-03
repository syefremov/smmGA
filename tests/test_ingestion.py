import pytest

from smm_gpt.core.config import Settings
from smm_gpt.workers.knowledge import poll as index_poll
from smm_gpt.workers.knowledge_files import poll as file_poll


async def test_disabled_ingestion_does_not_reconcile_or_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected(*args: object) -> None:
        pytest.fail("Disabled ingestion must not connect to a database, storage or scanner")

    monkeypatch.setattr("smm_gpt.workers.knowledge.Database", unexpected)
    monkeypatch.setattr("smm_gpt.workers.knowledge_files.Database", unexpected)
    assert await index_poll(Settings(_env_file=None)) == 0
    assert await file_poll(Settings(_env_file=None)) == 0
