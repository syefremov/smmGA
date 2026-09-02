"""Safe diagnostic tasks available during phase two."""

import asyncio
from datetime import UTC, datetime
from typing import TypedDict

from smm_gpt.workers.celery_app import celery_app
from smm_gpt.workers.knowledge import poll
from smm_gpt.workers.knowledge_files import poll as file_poll


class PingResult(TypedDict):
    status: str
    checked_at: str


@celery_app.task(name="smm_gpt.system.ping")
def system_ping() -> PingResult:
    """Return a timestamp without reading or mutating business data."""

    return {"status": "ok", "checked_at": datetime.now(UTC).isoformat()}


@celery_app.task(name="smm_gpt.knowledge.poll", ignore_result=True)
def knowledge_poll() -> None:
    """A timer wake-up carries no user data; durable work is claimed from PostgreSQL."""
    asyncio.run(poll())


@celery_app.task(name="smm_gpt.knowledge_files.poll", ignore_result=True)
def knowledge_files_poll() -> None:
    """Only a wake-up is sent to Redis, never files or credentials."""
    asyncio.run(file_poll())
