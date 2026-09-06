import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import enforce_rate_limit
from app.core.db import get_db_session
from app.core.config import get_settings
from app.core.redis_client import get_redis
from app.repositories.job_repository import JobRepository
from app.repositories.outbox_repository import OutboxRepository
from app.services.job_service import JobService
from app.schemas.job import JobCreateRequest, JobCreateResponse, JobStatusResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", response_model=JobCreateResponse, status_code=202)
async def submit_job(
    body: JobCreateRequest,
    db: AsyncSession = Depends(get_db_session),
    api_key=Depends(enforce_rate_limit),
):
    settings = get_settings()
    svc = JobService(JobRepository(db), OutboxRepository(db), settings.celery_max_retries)
    job = await svc.submit(api_key.id, body.prompt)
    return JobCreateResponse(job_id=job.id, status=job.status)


@router.get("/{job_id}", response_model=JobStatusResponse)
async def get_job_status(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    api_key=Depends(enforce_rate_limit),
):
    job = await JobRepository(db).get_by_id(job_id)
    if job is None or job.api_key_id != api_key.id:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        result_url=job.result_url,
        error_message=job.error_message,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.get("/{job_id}/stream")
async def stream_job_status(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    api_key=Depends(enforce_rate_limit),
):
    job = await JobRepository(db).get_by_id(job_id)
    if job is None or job.api_key_id != api_key.id:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator():
        redis = get_redis()
        pubsub = redis.pubsub()
        await pubsub.subscribe(f"job:{job_id}:status")
        try:
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message and message["type"] == "message":
                    data = json.loads(message["data"])
                    yield f"data: {json.dumps(data)}\n\n"
                    if data["status"] in ("COMPLETED", "FAILED"):
                        break
                await asyncio.sleep(0.1)
        finally:
            await pubsub.unsubscribe(f"job:{job_id}:status")
            await pubsub.close()

    return StreamingResponse(event_generator(), media_type="text/event-stream")