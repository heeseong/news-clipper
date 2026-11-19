"""Celery application.

Run one worker per queue so heavy work cannot starve the rest (spec 6):

    celery -A app.worker worker -Q media   -c 2 -n media@%h
    celery -A app.worker worker -Q align   -c 1 -n align@%h    # model stays in memory
    celery -A app.worker worker -Q ai      -c 4 -n ai@%h       # also runs orchestration callbacks
    celery -A app.worker worker -Q publish -c 2 -n publish@%h
"""

from __future__ import annotations

from celery import Celery
from celery.signals import setup_logging as celery_setup_logging
from kombu import Queue

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.pipeline.definition import CONTROL_QUEUE, QUEUES

settings = get_settings()

celery_app = Celery("news_clipper", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_queues=[Queue(q) for q in QUEUES],
    task_default_queue=CONTROL_QUEUE,
    # Reliability (spec 9): ack after the task finishes, so a crashed worker's task is redelivered.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    result_expires=24 * 3600,
    timezone="Asia/Seoul",
    enable_utc=True,
    task_always_eager=settings.celery_always_eager,
    task_eager_propagates=True,
    broker_connection_retry_on_startup=True,
    imports=["app.pipeline.tasks"],
)


@celery_setup_logging.connect
def _setup_logging(**_: object) -> None:
    # Use our JSON logging instead of Celery's default handlers.
    setup_logging(settings.log_level, settings.log_format)
