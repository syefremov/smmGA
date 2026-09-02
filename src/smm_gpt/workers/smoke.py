"""Send one safe task and verify that a worker returns its result."""

from typing import cast

from smm_gpt.workers.celery_app import celery_app
from smm_gpt.workers.tasks import PingResult


def main() -> None:
    result = celery_app.send_task("smm_gpt.system.ping")
    payload = cast(PingResult, result.get(timeout=15))
    if payload["status"] != "ok":
        raise RuntimeError("Worker smoke task returned an unexpected status")
    print("Worker smoke task completed successfully.")


if __name__ == "__main__":
    main()
