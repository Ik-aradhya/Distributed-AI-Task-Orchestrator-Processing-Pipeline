"""
test_submit_job.py

Scenario: Submit job
  POST /jobs → 202 → job row QUEUED → outbox row created (dispatched_at IS NULL)
"""

import pytest
from sqlalchemy import select

from app.models.job import Job, JobStatus
from app.models.outbox import Outbox


@pytest.mark.asyncio
async def test_submit_job_returns_202(app_client, api_key):
    _, raw_key = api_key

    resp = await app_client.post(
        "/jobs",
        json={"prompt": "a red fox in the snow"},
        headers={"X-API-Key": raw_key},
    )

    assert resp.status_code == 202
    body = resp.json()
    assert "job_id" in body
    assert body["status"] == JobStatus.QUEUED.value


@pytest.mark.asyncio
async def test_submit_job_creates_queued_row(app_client, api_key, db_session):
    _, raw_key = api_key

    resp = await app_client.post(
        "/jobs",
        json={"prompt": "a mountain at sunset"},
        headers={"X-API-Key": raw_key},
    )
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    # Check the DB row
    result = await db_session.execute(
        select(Job).where(Job.id == job_id)
    )
    job = result.scalar_one_or_none()

    assert job is not None, "Job row must exist in DB"
    assert job.status == JobStatus.QUEUED
    assert job.prompt == "a mountain at sunset"


@pytest.mark.asyncio
async def test_submit_job_creates_outbox_row(app_client, api_key, db_session):
    _, raw_key = api_key

    resp = await app_client.post(
        "/jobs",
        json={"prompt": "a futuristic city"},
        headers={"X-API-Key": raw_key},
    )
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    # Check outbox row
    result = await db_session.execute(
        select(Outbox).where(Outbox.job_id == job_id)
    )
    outbox = result.scalar_one_or_none()

    assert outbox is not None, "Outbox row must exist after job submission"
    assert outbox.dispatched_at is None, "Outbox row must be undispatched initially"
    assert outbox.event_type == "JOB_SUBMITTED"
    assert outbox.payload["job_id"] == job_id
    assert outbox.payload["prompt"] == "a futuristic city"
