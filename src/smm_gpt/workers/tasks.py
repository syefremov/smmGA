"""Safe diagnostic tasks available during phase two."""

from datetime import UTC, datetime
from typing import TypedDict

from smm_gpt.workers.celery_app import celery_app


class PingResult(TypedDict):
    status: str
    checked_at: str


@celery_app.task(name="smm_gpt.system.ping")
def system_ping() -> PingResult:
    """Return a timestamp without reading or mutating business data."""

    return {"status": "ok", "checked_at": datetime.now(UTC).isoformat()}
