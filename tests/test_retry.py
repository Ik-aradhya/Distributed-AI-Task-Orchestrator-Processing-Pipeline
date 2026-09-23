"""
test_retry.py

Scenario: Retryable failure → automatic retry → eventual success
  Attempt 1: provider raises ProviderError(retryable=True)
    → job FAILED → RETRYING, retry_count == 1, _RetrySignal raised
  Attempt 2: provider returns success
    → job COMPLETED
"""

import pytest
from sqlalchemy import select

from app.models.job import Job, JobStatus
from app.providers.base import ProviderError, ProviderResult
from tests.helpers import _RetrySignal, run_task_direct


RESULT_URL = "https://cdn.example.com/retry-success.png"


@pytest.mark.asyncio
async def test_retryable_failure_sets_retrying_status(
    app_client, api_key, db_session, make_provider_mock
):
    """After a retryable provider error the job must be RETRYING with retry_count=1."""
    _, raw_key = api_key

    resp = await app_client.post(
        "/jobs",
        json={"prompt": "a stormy ocean"},
        headers={"X-API-Key": raw_key},
    )
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]
    await db_session.commit()

    # Attempt 1: provider always fails with a retryable error
    make_provider_mock(side_effect=ProviderError("upstream timeout", retryable=True))

    with pytest.raises(_RetrySignal) as exc_info:
        await run_task_direct(job_id)

    retry_signal: _RetrySignal = exc_info.value
    assert isinstance(retry_signal.exc, ProviderError)
    assert retry_signal.countdown is not None  # backoff was set

    # Verify DB state after first attempt
    result = await db_session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one()
    await db_session.refresh(job)

    assert job.status == JobStatus.RETRYING
    assert job.retry_count == 1


@pytest.mark.asyncio
async def test_retry_then_success_completes_job(
    app_client, api_key, db_session, make_provider_mock
):
    """Simulate redelivery: after RETRYING state the second run must COMPLETE the job."""
    _, raw_key = api_key

    resp = await app_client.post(
        "/jobs",
        json={"prompt": "a calm forest"},
        headers={"X-API-Key": raw_key},
    )
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]
    await db_session.commit()

    # Attempt 1: fail
    make_provider_mock(side_effect=ProviderError("transient error", retryable=True))
    with pytest.raises(_RetrySignal):
        await run_task_direct(job_id)

    # Attempt 2: succeed (Celery would redeliver; we run the task body again directly)
    make_provider_mock(
        return_value=ProviderResult(
            image_url=RESULT_URL,
            raw_response={"url": RESULT_URL},
        )
    )
    await run_task_direct(job_id)  # must not raise

    result = await db_session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one()
    await db_session.refresh(job)

    assert job.status == JobStatus.COMPLETED
    assert job.result_url == RESULT_URL


@pytest.mark.asyncio
async def test_backoff_countdown_uses_exponential_formula(
    app_client, api_key, db_session, make_provider_mock
):
    """
    Verify the retry countdown follows  base * 2^(retry_index)
    where base = celery_backoff_base_seconds (default 2).
    First retry → countdown should be 2 * 2^0 = 2 seconds.
    """
    from app.core.config import get_settings

    _, raw_key = api_key
    settings = get_settings()

    resp = await app_client.post(
        "/jobs",
        json={"prompt": "a desert dune"},
        headers={"X-API-Key": raw_key},
    )
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]
    await db_session.commit()

    make_provider_mock(side_effect=ProviderError("timeout", retryable=True))

    with pytest.raises(_RetrySignal) as exc_info:
        await run_task_direct(job_id)

    expected_backoff = settings.celery_backoff_base_seconds * (2 ** 0)
    assert exc_info.value.countdown == expected_backoff
