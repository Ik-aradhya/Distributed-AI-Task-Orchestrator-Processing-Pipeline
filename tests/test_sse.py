"""
test_sse.py

Scenario: SSE streaming
  1. Normal case – open stream, task completes, client receives COMPLETED event.
  2. Already-completed race – job is COMPLETED before client subscribes; generator
     yields the terminal event immediately from DB without blocking.
"""

import asyncio
import json
import uuid

import pytest
from sqlalchemy import select

from app.models.job import Job, JobStatus
from app.providers.base import ProviderResult
from tests.helpers import run_task_direct

RESULT_URL = "https://cdn.example.com/sse-test.png"


async def _collect_sse_frames(client, job_id: str, raw_key: str, *, max_frames=5):
    """Collect SSE data frames from the streaming endpoint."""
    frames = []
    async with client.stream(
        "GET",
        f"/jobs/{job_id}/stream",
        headers={"X-API-Key": raw_key},
        timeout=10.0,
    ) as response:
        assert response.status_code == 200
        async for line in response.aiter_lines():
            if line.startswith("data:"):
                frames.append(json.loads(line[len("data:"):].strip()))
                status = frames[-1].get("status", "")
                if status in ("COMPLETED", "FAILED"):
                    break
            if len(frames) >= max_frames:
                break
    return frames


@pytest.mark.asyncio
async def test_sse_delivers_completed_event(
    app_client, api_key, db_session, redis_client, make_provider_mock
):
    """The SSE stream must deliver a COMPLETED event after the task finishes."""
    _, raw_key = api_key

    resp = await app_client.post(
        "/jobs",
        json={"prompt": "a steaming cup of coffee"},
        headers={"X-API-Key": raw_key},
    )
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]
    await db_session.commit()

    make_provider_mock(
        return_value=ProviderResult(
            image_url=RESULT_URL,
            raw_response={"url": RESULT_URL},
        )
    )

    # Run the task in the background while we consume the SSE stream
    async def _run_task():
        await asyncio.sleep(0.2)  # give the SSE subscriber time to register
        await run_task_direct(job_id)

    frames, _ = await asyncio.gather(
        _collect_sse_frames(app_client, job_id, raw_key),
        _run_task(),
    )

    terminal = next((f for f in frames if f.get("status") in ("COMPLETED", "FAILED")), None)
    assert terminal is not None, f"No terminal SSE frame received. Got: {frames}"
    assert terminal["status"] == "COMPLETED"
    assert terminal.get("result_url") == RESULT_URL


@pytest.mark.asyncio
async def test_sse_already_completed_returns_immediately(
    app_client, api_key, db_session, redis_client, make_provider_mock
):
    """
    Race-condition guard: if the job is already COMPLETED when the SSE client
    connects, the generator must yield the terminal event from DB and return
    without hanging on the pub/sub loop.
    """
    _, raw_key = api_key

    resp = await app_client.post(
        "/jobs",
        json={"prompt": "a snowy mountain peak"},
        headers={"X-API-Key": raw_key},
    )
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]
    await db_session.commit()

    # Complete the job BEFORE the SSE client connects
    make_provider_mock(
        return_value=ProviderResult(
            image_url=RESULT_URL,
            raw_response={"url": RESULT_URL},
        )
    )
    await run_task_direct(job_id)

    # Now open SSE – should get COMPLETED immediately (no hanging)
    frames = await asyncio.wait_for(
        _collect_sse_frames(app_client, job_id, raw_key),
        timeout=5.0,
    )

    terminal = next((f for f in frames if f.get("status") in ("COMPLETED", "FAILED")), None)
    assert terminal is not None, f"No terminal frame. Got: {frames}"
    assert terminal["status"] == "COMPLETED"
