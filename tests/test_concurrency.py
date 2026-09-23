import asyncio
import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.main import app
from app.models.job import Job, JobStatus
from app.models.outbox import Outbox
from app.repositories.job_repository import JobRepository
from app.repositories.outbox_repository import OutboxRepository
from app.services.job_service import JobService
from app.services.exceptions import InvalidTransition
from app.relay.outbox_relay import poll_cycle
from app.workers.tasks import _async_generate_image_task


@pytest.mark.anyio
async def test_simultaneous_job_submissions(db_session: AsyncSession, redis_client, api_key, make_provider_mock):
    """Test submitting 50 jobs simultaneously via concurrent HTTP requests."""
    key_row, raw_key = api_key
    headers = {"X-API-Key": raw_key}

    async def submit_one(i: int):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            return await client.post("/jobs", json={"prompt": f"Concurrent prompt {i}"}, headers=headers)

    responses = await asyncio.gather(*[submit_one(i) for i in range(50)])

    # All should return 202 Accepted
    assert all(r.status_code == 202 for r in responses)
    job_ids = {r.json()["job_id"] for r in responses}
    assert len(job_ids) == 50

    # Verify 50 job records created in DB
    result = await db_session.execute(select(func.count(Job.id)))
    assert result.scalar() == 50

    # Verify 50 outbox records created in DB
    outbox_count = await db_session.execute(select(func.count(Outbox.id)))
    assert outbox_count.scalar() == 50


@pytest.mark.anyio
async def test_concurrent_updates_to_same_job(test_engine, api_key):
    """Test 10 concurrent worker tasks trying to start_processing on the exact same job."""
    key_row, _ = api_key
    job_id = uuid.uuid4()

    # Create 1 job in QUEUED status
    async_session = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    async with async_session() as session:
        job_repo = JobRepository(session)
        outbox_repo = OutboxRepository(session)
        svc = JobService(job_repo, outbox_repo, max_retries=3)

        job = job_repo.create(job_id, key_row.id, "Concurrent update prompt")
        svc._assert_transition(JobStatus.PENDING, JobStatus.QUEUED)
        job_repo.update_status(job, JobStatus.QUEUED)
        await session.commit()

    # Attempt start_processing concurrently from 10 parallel sessions
    results = []

    async def attempt_start(index: int):
        async with async_session() as session:
            job_repo = JobRepository(session)
            outbox_repo = OutboxRepository(session)
            svc = JobService(job_repo, outbox_repo, max_retries=3)
            try:
                processed_job = await svc.start_processing(job_id)
                await session.commit()
                results.append(("success", index, processed_job.status))
            except InvalidTransition as e:
                results.append(("failed", index, str(e)))

    await asyncio.gather(*[attempt_start(i) for i in range(10)])

    # All 10 tasks reach PROCESSING state safely due to get_for_update row lock & crash-recovery idempotency
    assert len(results) == 10
    assert all(r[2] == JobStatus.PROCESSING for r in results)

    # Mark job COMPLETED
    async with async_session() as session:
        job_repo = JobRepository(session)
        outbox_repo = OutboxRepository(session)
        svc = JobService(job_repo, outbox_repo, max_retries=3)
        await svc.complete(job_id, "https://cdn.example.com/done.png")
        await session.commit()

    # Now attempt start_processing on COMPLETED job concurrently — all 10 MUST fail with InvalidTransition
    terminal_results = []

    async def attempt_terminal_start():
        async with async_session() as session:
            job_repo = JobRepository(session)
            outbox_repo = OutboxRepository(session)
            svc = JobService(job_repo, outbox_repo, max_retries=3)
            try:
                await svc.start_processing(job_id)
            except InvalidTransition:
                terminal_results.append("invalid_transition")

    await asyncio.gather(*[attempt_terminal_start() for _ in range(10)])
    assert len(terminal_results) == 10


@pytest.mark.anyio
async def test_outbox_concurrency_skip_locked(test_engine, api_key, mocker):
    """Test 5 concurrent outbox pollers with FOR UPDATE SKIP LOCKED picking up 50 rows."""
    key_row, _ = api_key
    mock_send = mocker.patch("app.relay.outbox_relay.celery_app.send_task")

    # Insert 50 jobs and 50 outbox entries
    async_session = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    async with async_session() as session:
        job_repo = JobRepository(session)
        outbox_repo = OutboxRepository(session)
        for i in range(50):
            jid = uuid.uuid4()
            job_repo.create(jid, key_row.id, f"Prompt {i}")
            outbox_repo.create(
                job_id=jid,
                event_type="JOB_SUBMITTED",
                payload={"job_id": str(jid), "prompt": f"Prompt {i}"},
            )
        await session.commit()

    # Run 5 concurrent outbox poller cycles
    await asyncio.gather(*[poll_cycle() for _ in range(5)])

    # All 50 outbox events must be dispatched exactly once
    assert mock_send.call_count == 50

    # Verify in DB that all 50 outbox items are marked dispatched
    async with async_session() as session:
        result = await session.execute(select(func.count()).select_from(Outbox).where(Outbox.dispatched == False))
        assert result.scalar() == 0


@pytest.mark.anyio
async def test_rate_limiting_under_concurrent_burst(redis_client, api_key, monkeypatch):
    """Test 30 burst requests against a rate limit of 10 requests/min."""
    key_row, raw_key = api_key
    headers = {"X-API-Key": raw_key}

    from app.core.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_requests_per_minute", 10)

    async def make_req():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            return await client.post("/jobs", json={"prompt": "Rate limit burst test"}, headers=headers)

    responses = await asyncio.gather(*[make_req() for _ in range(30)])

    successes = [r for r in responses if r.status_code == 202]
    rate_limited = [r for r in responses if r.status_code == 429]

    assert len(successes) == 10
    assert len(rate_limited) == 20


@pytest.mark.anyio
async def test_multiple_workers_processing_jobs(test_engine, api_key, make_provider_mock, redis_client):
    """Test multiple worker tasks concurrently executing distinct jobs."""
    make_provider_mock()
    key_row, _ = api_key
    job_ids = [uuid.uuid4() for _ in range(20)]

    # Populate 20 queued jobs
    async_session = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    async with async_session() as session:
        job_repo = JobRepository(session)
        outbox_repo = OutboxRepository(session)
        svc = JobService(job_repo, outbox_repo, max_retries=3)
        for jid in job_ids:
            job = job_repo.create(jid, key_row.id, f"Worker test prompt {jid}")
            svc._assert_transition(JobStatus.PENDING, JobStatus.QUEUED)
            job_repo.update_status(job, JobStatus.QUEUED)
        await session.commit()

    # Process all 20 jobs using worker task runner concurrently
    mock_task = type("Task", (), {"retry": Exception})()

    await asyncio.gather(*[_async_generate_image_task(mock_task, str(jid)) for jid in job_ids])

    # Check that all 20 jobs are now COMPLETED in DB
    async with async_session() as session:
        result = await session.execute(select(func.count()).select_from(Job).where(Job.status == JobStatus.COMPLETED))
        assert result.scalar() == 20
