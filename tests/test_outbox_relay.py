"""
test_outbox_relay.py

Scenario: Outbox → Celery
  Insert outbox row → run poll_cycle() → outbox row marked dispatched → Celery task sent
"""

import uuid

import pytest
from sqlalchemy import select
from unittest.mock import patch, call

from app.models.job import Job, JobStatus
from app.models.outbox import Outbox
from app.relay.outbox_relay import poll_cycle


async def _make_job_and_outbox(db_session, api_key_row) -> tuple[Job, Outbox]:
    """Helper: insert a minimal QUEUED job + outbox row directly into the DB."""
    job_id = uuid.uuid4()
    job = Job(
        id=job_id,
        api_key_id=api_key_row.id,
        prompt="a test prompt",
        status=JobStatus.QUEUED,
    )
    db_session.add(job)
    await db_session.flush()

    outbox = Outbox(
        job_id=job_id,
        event_type="JOB_SUBMITTED",
        payload={"job_id": str(job_id), "prompt": "a test prompt"},
        dispatched_at=None,
    )
    db_session.add(outbox)
    await db_session.flush()
    return job, outbox


@pytest.mark.asyncio
async def test_relay_sends_task_to_celery(db_session, api_key):
    api_key_row, _ = api_key
    job, outbox = await _make_job_and_outbox(db_session, api_key_row)
    await db_session.commit()  # relay uses its own session, so we must commit first

    with patch("app.relay.outbox_relay.celery_app") as mock_celery:
        await poll_cycle()
        mock_celery.send_task.assert_called_once_with(
            "generate_image", args=[str(job.id)]
        )


@pytest.mark.asyncio
async def test_relay_marks_outbox_dispatched(db_session, api_key):
    api_key_row, _ = api_key
    job, outbox = await _make_job_and_outbox(db_session, api_key_row)
    outbox_id = outbox.id
    await db_session.commit()

    with patch("app.relay.outbox_relay.celery_app"):
        await poll_cycle()

    # Re-query to see committed state
    result = await db_session.execute(
        select(Outbox).where(Outbox.id == outbox_id).execution_options(populate_existing=True)
    )
    refreshed = result.scalar_one()
    assert refreshed.dispatched_at is not None, "Outbox row must be marked dispatched"


@pytest.mark.asyncio
async def test_relay_does_not_redispatch_already_dispatched(db_session, api_key):
    """A second poll_cycle() call must NOT re-send already-dispatched rows."""
    api_key_row, _ = api_key
    job, outbox = await _make_job_and_outbox(db_session, api_key_row)
    await db_session.commit()

    with patch("app.relay.outbox_relay.celery_app") as mock_celery:
        await poll_cycle()  # first dispatch
        await poll_cycle()  # second call – should be a no-op

        # send_task must have been called exactly once total
        assert mock_celery.send_task.call_count == 1
