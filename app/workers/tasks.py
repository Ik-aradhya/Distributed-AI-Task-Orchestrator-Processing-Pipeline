# app/workers/tasks.py
import json, uuid
from app.workers.celery_app import celery_app
from app.core.db import get_session
from app.core.redis_client import get_redis
from app.core.config import get_settings
from app.repositories.job_repository import JobRepository
from app.repositories.outbox_repository import OutboxRepository
from app.services.job_service import JobService
from app.services.exceptions import InvalidTransition, JobNotFound
from app.providers.hosted_provider import HostedProvider
from app.providers.base import ProviderError

def _publish_status(job_id: str, status: str, **extra):
    get_redis().publish(f"job:{job_id}:status", json.dumps({"job_id": job_id, "status": status, **extra}))

@celery_app.task(bind=True, name="generate_image")
def generate_image_task(self, job_id: str):
    settings = get_settings()

    with get_session() as session:
        svc = JobService(JobRepository(session), OutboxRepository(session), settings.celery_max_retries)
        try:
            job = svc.start_processing(uuid.UUID(job_id))
            prompt = job.prompt
        except (InvalidTransition, JobNotFound):
            return  # duplicate delivery on an already-handled job -> safe no-op

    _publish_status(job_id, "PROCESSING")

    try:
        result = HostedProvider().generate(prompt)
    except ProviderError as e:
        with get_session() as session:
            svc = JobService(JobRepository(session), OutboxRepository(session), settings.celery_max_retries)
            job = svc.fail(uuid.UUID(job_id), str(e), retryable=e.retryable)
        _publish_status(job_id, job.status, error=str(e))
        if job.status == "RETRYING":
            backoff = settings.celery_backoff_base_seconds * (2 ** self.request.retries)
            raise self.retry(exc=e, countdown=backoff, max_retries=settings.celery_max_retries)
        return

    with get_session() as session:
        svc = JobService(JobRepository(session), OutboxRepository(session), settings.celery_max_retries)
        svc.complete(uuid.UUID(job_id), result.image_url)
    _publish_status(job_id, "COMPLETED", result_url=result.image_url)