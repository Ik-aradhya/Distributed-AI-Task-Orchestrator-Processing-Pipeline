# app/workers/tasks.py
import asyncio
import json
import uuid

from celery.signals import worker_process_init, worker_process_shutdown

from app.workers.celery_app import celery_app
from app.core.db import AsyncSessionLocal
from app.core.redis_client import get_redis
from app.core.config import get_settings
from app.repositories.job_repository import JobRepository
from app.repositories.outbox_repository import OutboxRepository
from app.services.job_service import JobService
from app.services.exceptions import InvalidTransition, JobNotFound
from app.providers.hosted_provider import HostedProvider
from app.providers.base import ProviderError

_loop: asyncio.AbstractEventLoop | None = None
_provider: HostedProvider | None = None


def _get_event_loop() -> asyncio.AbstractEventLoop:
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop


def _get_provider() -> HostedProvider:
    global _provider
    if _provider is None:
        _provider = HostedProvider()
    return _provider


@worker_process_init.connect
def init_worker_process(**kwargs):
    global _loop, _provider
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _provider = HostedProvider()


@worker_process_shutdown.connect
def shutdown_worker_process(**kwargs):
    global _loop, _provider
    if _provider and _loop and not _loop.is_closed():
        _loop.run_until_complete(_provider.close())
        _provider = None
    if _loop and not _loop.is_closed():
        _loop.close()
        _loop = None


async def _publish_status(job_id: str, status: str, **extra):
    redis = get_redis()
    await redis.publish(
        f"job:{job_id}:status",
        json.dumps({"job_id": job_id, "status": status, **extra}),
    )


async def _async_generate_image_task(task, job_id: str):
    settings = get_settings()

    async with AsyncSessionLocal() as session:
        svc = JobService(JobRepository(session), OutboxRepository(session), settings.celery_max_retries)
        try:
            job = await svc.start_processing(uuid.UUID(job_id))
            prompt = job.prompt
            await session.commit()
        except (InvalidTransition, JobNotFound):
            return  # duplicate delivery on an already-handled job -> safe no-op

    await _publish_status(job_id, "PROCESSING")

    provider = _get_provider()
    try:
        result = await provider.generate(prompt)
    except ProviderError as e:
        async with AsyncSessionLocal() as session:
            svc = JobService(JobRepository(session), OutboxRepository(session), settings.celery_max_retries)
            job = await svc.fail(uuid.UUID(job_id), str(e), retryable=e.retryable)
            await session.commit()

        await _publish_status(job_id, job.status, error=str(e))
        if job.status == "RETRYING":
            retry_index = max(0, job.retry_count - 1)
            backoff = settings.celery_backoff_base_seconds * (2 ** retry_index)
            raise task.retry(exc=e, countdown=backoff, max_retries=settings.celery_max_retries)
        return

    async with AsyncSessionLocal() as session:
        svc = JobService(JobRepository(session), OutboxRepository(session), settings.celery_max_retries)
        await svc.complete(uuid.UUID(job_id), result.image_url)
        await session.commit()

    await _publish_status(job_id, "COMPLETED", result_url=result.image_url)


@celery_app.task(bind=True, name="generate_image")
def generate_image_task(self, job_id: str):
    loop = _get_event_loop()
    return loop.run_until_complete(_async_generate_image_task(self, job_id))