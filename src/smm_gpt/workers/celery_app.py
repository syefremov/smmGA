"""Celery configuration shared by worker and scheduler containers."""

from celery import Celery

from smm_gpt.core.config import get_settings

settings = get_settings()
redis_url = settings.redis_url.get_secret_value()

celery_app = Celery("smm_gpt", broker=redis_url, backend=redis_url)
celery_app.conf.update(
    accept_content=["json"],
    beat_schedule={
        "phase-two-heartbeat": {
            "task": "smm_gpt.system.ping",
            "schedule": 300.0,
        },
        "knowledge-queue": {"task": "smm_gpt.knowledge.poll", "schedule": 30.0},
        "knowledge-files": {"task": "smm_gpt.knowledge_files.poll", "schedule": 30.0},
        "ai-queue": {"task": "smm_gpt.ai.poll", "schedule": 30.0},
    },
    enable_utc=True,
    imports=("smm_gpt.workers.tasks",),
    result_expires=600,
    task_serializer="json",
    result_serializer="json",
    timezone="UTC",
    worker_hijack_root_logger=False,
)
