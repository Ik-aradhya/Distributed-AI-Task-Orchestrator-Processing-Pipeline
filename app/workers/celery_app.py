# app/workers/celery_app.py
from celery import Celery
from app.core.config import get_settings

settings = get_settings()
celery_app = Celery("image_orchestrator", broker=str(settings.redis_url))

celery_app.conf.update(
    task_acks_late=True,            # ack only after task finishes -> crash mid-task redelivers it
    worker_prefetch_multiplier=1,   # don't let a crashed worker strand hoarded tasks
    task_reject_on_worker_lost=True,
    task_track_started=True,
)