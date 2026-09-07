"""Celery application entry point (STORY 0.1 scaffold — the real job
infrastructure, queues, and beat schedule land in STORY 0.3). This
module just needs to boot cleanly so `docker-compose up`'s worker
service has a real, running process rather than crash-looping."""

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "expense_tracker",
    broker=str(settings.REDIS_URI),
    backend=str(settings.REDIS_URI),
)

celery_app.conf.update(task_serializer="json", accept_content=["json"], result_serializer="json")
