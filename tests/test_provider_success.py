"""
test_provider_success.py

Scenario: Full happy path
  Submit job → relay → run task (in-process, provider mocked)
  → job COMPLETED, result_url set, Redis pub/sub COMPLETED published
"""

import asyncio
import json
import uuid

import pytest
from sqlalchemy import select

from app.models.job import Job, JobStatus
from app.providers.base import ProviderResult
from tests.helpers import run_relay_once, run_task_direct


PROMPT = "a golden retriever on a beach"
RESULT_URL = "https://cdn.example.com/golden-retriever.png"


@pytest.mark.asyncio
async def test_provider_success_completes_job(
    app_client, api_key, db_session, redis_client, make_provider_mock
):
    _, raw_key = api_key

    # 1. Submit job through the API
    resp = await app_client.post(
        "/jobs",
        json={"prompt": PROMPT},
        headers={"X-API-Key": raw_key},
    )
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    # 2. Commit so the relay's own session can see the outbox row
    await db_session.commit()

    # 3. Subscribe to the Redis channel BEFORE running the task (avoid race)
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(f"job:{job_id}:status")
    await asyncio.sleep(0.05)  # give pubsub time to register

    # 4. Mock the provider to return a successful result
    make_provider_mock(
        return_value=ProviderResult(
            image_url=RESULT_URL,
            raw_response={"url": RESULT_URL},
        )
    )

    # 5. Run the relay to dispatch the Celery task (patched – no broker needed)
    from unittest.mock import patch
    with patch("app.relay.outbox_relay.celery_app"):
        await run_relay_once()

    # 6. Run the task body directly in-process
    await run_task_direct(job_id)

    # 7. Verify DB state
    result = await db_session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one()
    await db_session.refresh(job)

    assert job.status == JobStatus.COMPLETED
    assert job.result_url == RESULT_URL

    # 8. Verify Redis pub/sub message
    message = None
    for _ in range(20):
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
        if msg and msg["type"] == "message":
            data = json.loads(msg["data"])
            if data.get("status") == "COMPLETED":
                message = data
                break
        await asyncio.sleep(0.05)

    assert message is not None, "Expected a Redis pub/sub message but got none"
    assert message["status"] == "COMPLETED"
    assert message["result_url"] == RESULT_URL

    await pubsub.unsubscribe(f"job:{job_id}:status")
    await pubsub.aclose()
