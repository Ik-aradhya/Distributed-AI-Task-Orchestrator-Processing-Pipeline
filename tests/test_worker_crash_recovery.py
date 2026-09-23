"""
test_worker_crash_recovery.py

Scenario: Worker crash recovery
  A previous worker died mid-task leaving the job in PROCESSING status.
  When Celery redelivers the task (task_acks_late + task_reject_on_worker_lost),
  start_processing() detects the existing PROCESSING status and re-enters
  without raising InvalidTransition.  The task continues to COMPLETED.
"""

import uuid

import pytest
from sqlalchemy import select

from app.models.job import Job, JobStatus
from app.providers.base import ProviderResult
from tests.helpers import run_task_direct


RESULT_URL = "https://cdn.example.com/crash-recovery.png"


@pytest.mark.asyncio
async def test_crash_recovery_redelivery_completes_job(
    app_client, api_key, db_session, make_provider_mock
):
    """
    Simulate a crash by manually forcing the job to PROCESSING status in the DB.
    Re-running the task must succeed (no InvalidTransition) and complete the job.
    """
    _, raw_key = api_key

    # 1. Submit the job normally
    resp = await app_client.post(
        "/jobs",
        json={"prompt": "a crashed worker recovers"},
        headers={"X-API-Key": raw_key},
    )
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    # 2. Force job to PROCESSING (simulates a worker that died mid-run)
    result = await db_session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one()
    job.status = JobStatus.PROCESSING
    await db_session.commit()

    # 3. Mock provider to return success on redelivery
    make_provider_mock(
        return_value=ProviderResult(
            image_url=RESULT_URL,
            raw_response={"url": RESULT_URL},
        )
    )

    # 4. Re-run the task (simulates Celery redelivery after crash)
    # Must NOT raise InvalidTransition
    await run_task_direct(job_id)

    # 5. Verify the job completed correctly
    result = await db_session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one()
    await db_session.refresh(job)

    assert job.status == JobStatus.COMPLETED
    assert job.result_url == RESULT_URL


@pytest.mark.asyncio
async def test_crash_recovery_does_not_double_process(
    app_client, api_key, db_session, make_provider_mock
):
    """
    Two concurrent redeliveries of the same task: only one should proceed to
    provider.generate(); the other should exit quietly due to the row-level lock.

    In this test we run both sequentially (true concurrency would need two
    processes), verifying that the second call does not trigger a duplicate
    provider call or status error.
    """
    _, raw_key = api_key

    resp = await app_client.post(
        "/jobs",
        json={"prompt": "concurrent redelivery"},
        headers={"X-API-Key": raw_key},
    )
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]
    await db_session.commit()

    mock_provider = make_provider_mock(
        return_value=ProviderResult(
            image_url=RESULT_URL,
            raw_response={"url": RESULT_URL},
        )
    )

    # First run: QUEUED → PROCESSING → COMPLETED
    await run_task_direct(job_id)

    # Second run: job is already COMPLETED – start_processing() should raise
    # InvalidTransition (caught internally), and the task exits without calling
    # provider.generate() again.
    generate_calls_before = mock_provider.generate.call_count
    await run_task_direct(job_id)  # must not raise
    generate_calls_after = mock_provider.generate.call_count

    assert generate_calls_after == generate_calls_before, (
        "Provider.generate() must not be called again for an already-COMPLETED job"
    )
