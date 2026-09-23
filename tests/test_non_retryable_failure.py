"""
test_non_retryable_failure.py

Scenario: Non-retryable provider failure (e.g. 400 bad request)
  provider raises ProviderError(retryable=False)
  → job FAILED (terminal), retry_count unchanged, no retry signal
"""

import asyncio
import json

import pytest
from sqlalchemy import select

from app.models.job import Job, JobStatus
from app.providers.base import ProviderError
from tests.helpers import run_task_direct


@pytest.mark.asyncio
async def test_non_retryable_failure_sets_terminal_failed(
    app_client, api_key, db_session, make_provider_mock
):
    """A non-retryable error must leave the job in terminal FAILED state."""
    _, raw_key = api_key

    resp = await app_client.post(
        "/jobs",
        json={"prompt": "an invalid prompt that the provider rejects"},
        headers={"X-API-Key": raw_key},
    )
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]
    await db_session.commit()

    make_provider_mock(
        side_effect=ProviderError("Provider rejected request: 400", retryable=False)
    )

    # Must NOT raise _RetrySignal – task should complete normally (no retry)
    await run_task_direct(job_id)

    result = await db_session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one()
    await db_session.refresh(job)

    assert job.status == JobStatus.FAILED
    assert job.retry_count == 0  # unchanged
    assert "400" in (job.error_message or "")


@pytest.mark.asyncio
async def test_non_retryable_failure_publishes_failed_event(
    app_client, api_key, db_session, redis_client, make_provider_mock
):
    """A non-retryable failure must publish a FAILED status event to Redis."""
    _, raw_key = api_key

    resp = await app_client.post(
        "/jobs",
        json={"prompt": "another bad prompt"},
        headers={"X-API-Key": raw_key},
    )
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]
    await db_session.commit()

    pubsub = redis_client.pubsub()
    await pubsub.subscribe(f"job:{job_id}:status")
    await asyncio.sleep(0.05)

    make_provider_mock(
        side_effect=ProviderError("bad request", retryable=False)
    )
    await run_task_direct(job_id)

    message = None
    for _ in range(20):
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
        if msg and msg["type"] == "message":
            message = json.loads(msg["data"])
            if message.get("status") == "FAILED":
                break
        await asyncio.sleep(0.05)

    assert message is not None, "Expected a FAILED Redis message"
    assert message["status"] == "FAILED"
    assert "error" in message

    await pubsub.unsubscribe(f"job:{job_id}:status")
    await pubsub.aclose()


@pytest.mark.asyncio
async def test_retries_exhausted_becomes_terminal_failed(
    app_client, api_key, db_session, make_provider_mock, monkeypatch
):
    """
    When max_retries is reached, a retryable error must produce terminal FAILED
    (not RETRYING) and must NOT raise _RetrySignal.
    """
    from app.core import config as config_module

    # Override settings so max_retries = 0
    original_settings = config_module.get_settings()
    class _PatchedSettings:
        database_url = original_settings.database_url
        redis_url = original_settings.redis_url
        provider_api_key = original_settings.provider_api_key
        provider_base_url = original_settings.provider_base_url
        log_level = original_settings.log_level
        celery_max_retries = 0
        celery_backoff_base_seconds = 2
        rate_limit_requests_per_minute = 60
        outbox_poll_interval_ms = 200

    import app.workers.tasks as tasks_mod
    monkeypatch.setattr(config_module, "get_settings", lambda: _PatchedSettings())
    monkeypatch.setattr(tasks_mod, "get_settings", lambda: _PatchedSettings())

    _, raw_key = api_key

    resp = await app_client.post(
        "/jobs",
        json={"prompt": "exhausted retries prompt"},
        headers={"X-API-Key": raw_key},
    )
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]
    await db_session.commit()

    make_provider_mock(side_effect=ProviderError("timeout", retryable=True))

    # max_retries=0 → no retry, task must return without _RetrySignal
    await run_task_direct(job_id)

    result = await db_session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one()
    await db_session.refresh(job)

    assert job.status == JobStatus.FAILED
