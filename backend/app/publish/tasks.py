"""Celery tasks on the `publish` queue (plan M7). The sweep runs every minute from Celery beat."""

from __future__ import annotations

from app.worker import celery_app

from . import service


@celery_app.task(name="publish.upload")
def upload(task_id: int) -> str:
    return service.run_upload(task_id)


@celery_app.task(name="publish.thumbnail")
def thumbnail(task_id: int) -> str:
    return service.set_thumbnail(task_id)


@celery_app.task(name="publish.check")
def check(task_id: int) -> str:
    return service.check_status(task_id)


@celery_app.task(name="publish.sweep")
def sweep() -> dict[str, int]:
    return service.sweep()
