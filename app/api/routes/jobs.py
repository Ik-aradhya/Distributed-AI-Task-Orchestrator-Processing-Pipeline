# app/api/routes/jobs.py
import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import enforce_rate_limit
from app.core.db import AsyncSessionLocal, get_db_session
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.redis_client import get_redis
from app.models.job import JobStatus
from app.repositories.job_repository import JobRepository
from app.repositories.outbox_repository import OutboxRepository
from app.services.job_service import JobService
from app.schemas.job import (
    ErrorResponse,
    JobCreateRequest,
    JobCreateResponse,
    JobStatusResponse,
    JobStatusStreamEvent,
)

logger = get_logger("jobs_api")
router = APIRouter(prefix="/jobs", tags=["Jobs"])


@router.post(
    "",
    response_model=JobCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit an asynchronous image generation job",
    description=(
        "Submits a text prompt for image generation. The job is persisted to PostgreSQL and committed "
        "alongside an Outbox event in an atomic transaction. Returns immediately with HTTP 202 Accepted and a unique job ID."
    ),
    responses={
        202: {"description": "Job successfully queued.", "model": JobCreateResponse},
        400: {"description": "Malformed request or validation error.", "model": ErrorResponse},
        401: {"description": "Missing or invalid API Key in X-API-Key header.", "model": ErrorResponse},
        429: {"description": "Rate limit exceeded (too many requests per minute).", "model": ErrorResponse},
    },
)
async def submit_job(
    body: JobCreateRequest,
    db: AsyncSession = Depends(get_db_session),
    api_key=Depends(enforce_rate_limit),
):
    settings = get_settings()
    svc = JobService(JobRepository(db), OutboxRepository(db), settings.celery_max_retries)
    job = svc.submit(api_key.id, body.prompt)
    await db.commit()
    logger.info("job_submitted", job_id=str(job.id), api_key_id=str(api_key.id), status=job.status.value)
    return JobCreateResponse(job_id=job.id, status=job.status)


@router.get(
    "/{job_id}",
    response_model=JobStatusResponse,
    summary="Get current job status and results",
    description=(
        "Fetches the latest execution status, timestamps, resulting image URL (if completed), "
        "or error details (if failed) for the specified job ID. Scoped to the authenticated API key."
    ),
    responses={
        200: {"description": "Job details retrieved successfully.", "model": JobStatusResponse},
        401: {"description": "Missing or invalid API Key.", "model": ErrorResponse},
        404: {"description": "Job not found or unauthorized.", "model": ErrorResponse},
        429: {"description": "Rate limit exceeded.", "model": ErrorResponse},
    },
)
async def get_job_status(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    api_key=Depends(enforce_rate_limit),
):
    job = await JobRepository(db).get_by_id(job_id)
    if job is None or job.api_key_id != api_key.id:
        logger.warning("job_lookup_not_found", job_id=str(job_id), api_key_id=str(api_key.id))
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        result_url=job.result_url,
        error_message=job.error_message,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.get(
    "/{job_id}/stream",
    summary="Stream real-time job status updates via Server-Sent Events (SSE)",
    description=(
        "Establishes a persistent SSE connection (`text/event-stream`) streaming real-time status transitions "
        "(QUEUED -> PROCESSING -> COMPLETED / FAILED) backed by Redis Pub/Sub. Automatically terminates when the job reaches a terminal state."
    ),
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "SSE stream delivering real-time JSON events (`data: {...}\\n\\n`).",
            "content": {
                "text/event-stream": {
                    "schema": {
                        "type": "string",
                        "example": 'data: {"job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "status": "COMPLETED", "result_url": "https://storage.googleapis.com/..."}\n\n',
                    }
                }
            },
        },
        401: {"description": "Missing or invalid API Key.", "model": ErrorResponse},
        404: {"description": "Job not found or unauthorized.", "model": ErrorResponse},
        429: {"description": "Rate limit exceeded.", "model": ErrorResponse},
    },
)
async def stream_job_status(
    job_id: uuid.UUID,
    api_key=Depends(enforce_rate_limit),
):
    async with AsyncSessionLocal() as session:
        job = await JobRepository(session).get_by_id(job_id)
        if job is None or job.api_key_id != api_key.id:
            logger.warning("sse_stream_unauthorized_or_not_found", job_id=str(job_id), api_key_id=str(api_key.id))
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    logger.info("sse_stream_connected", job_id=str(job_id), api_key_id=str(api_key.id))

    async def event_generator():
        redis = get_redis()
        pubsub = redis.pubsub()
        await pubsub.subscribe(f"job:{job_id}:status")
        try:
            # Check DB state after subscribing to eliminate race conditions
            async with AsyncSessionLocal() as session:
                current_job = await JobRepository(session).get_by_id(job_id)

            if current_job and current_job.status in (JobStatus.COMPLETED, JobStatus.FAILED):
                payload = {
                    "job_id": str(current_job.id),
                    "status": current_job.status.value if hasattr(current_job.status, "value") else str(current_job.status),
                }
                if current_job.result_url:
                    payload["result_url"] = current_job.result_url
                if current_job.error_message:
                    payload["error"] = current_job.error_message
                yield f"data: {json.dumps(payload)}\n\n"
                return

            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message and message["type"] == "message":
                    data = json.loads(message["data"])
                    yield f"data: {json.dumps(data)}\n\n"
                    if data.get("status") in ("COMPLETED", "FAILED"):
                        break
                else:
                    # SSE comment heartbeat to prevent reverse-proxy timeout (Nginx/Cloudflare/ALB)
                    yield ": ping\n\n"
                await asyncio.sleep(0.1)
        finally:
            await pubsub.unsubscribe(f"job:{job_id}:status")
            await pubsub.aclose()
            logger.info("sse_stream_closed", job_id=str(job_id))

    return StreamingResponse(event_generator(), media_type="text/event-stream")