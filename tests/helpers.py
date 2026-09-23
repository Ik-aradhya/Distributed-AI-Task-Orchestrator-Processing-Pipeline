"""
helpers.py – small utilities shared across integration tests.
"""

import asyncio
import uuid
from typing import Any
from unittest.mock import MagicMock


async def await_job_status(
    client,
    job_id: str | uuid.UUID,
    target_status: str,
    raw_api_key: str,
    *,
    timeout: float = 5.0,
    poll_interval: float = 0.1,
) -> dict[str, Any]:
    """
    Poll GET /jobs/{job_id} until the job reaches `target_status` or the
    timeout expires.  Returns the final JSON response body.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        resp = await client.get(
            f"/jobs/{job_id}",
            headers={"X-API-Key": raw_api_key},
        )
        resp.raise_for_status()
        body = resp.json()
        if body["status"] == target_status:
            return body
        if asyncio.get_event_loop().time() >= deadline:
            raise TimeoutError(
                f"Job {job_id} did not reach {target_status!r} within {timeout}s. "
                f"Last status: {body['status']!r}"
            )
        await asyncio.sleep(poll_interval)


async def run_relay_once() -> None:
    """Run a single outbox relay poll cycle directly (no subprocess)."""
    from app.relay.outbox_relay import poll_cycle

    await poll_cycle()


async def run_task_direct(job_id: str | uuid.UUID) -> None:
    """
    Execute the Celery task body directly inside the current event loop,
    bypassing the broker.  The task's `self` is a lightweight stub.
    """
    from app.workers.tasks import _async_generate_image_task

    class _FakeTask:
        """Minimal Celery task stub used in eager-mode execution."""

        request = MagicMock()
        max_retries = 5

        def retry(self, *, exc=None, countdown=None, max_retries=None):
            # Record the retry call so tests can assert on it, but don't
            # actually re-schedule anything.
            raise _RetrySignal(exc=exc, countdown=countdown)

    await _async_generate_image_task(_FakeTask(), str(job_id))


class _RetrySignal(Exception):
    """Raised by FakeTask.retry() so tests can detect and inspect retry calls."""

    def __init__(self, exc=None, countdown=None):
        self.exc = exc
        self.countdown = countdown
        super().__init__(repr(exc))
