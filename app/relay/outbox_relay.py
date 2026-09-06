# app/relay/outbox_relay.py
import asyncio
import logging
import signal

from app.core.config import get_settings
from app.core.db import AsyncSessionLocal
from app.repositories.outbox_repository import OutboxRepository
from app.workers.celery_app import celery_app

logger = logging.getLogger("outbox_relay")
_running = True


def _handle_shutdown(signum, frame):
    global _running
    logger.info("Shutdown signal received, finishing current cycle")
    _running = False


def dispatch_row(row):
    job_id = row.payload["job_id"]
    celery_app.send_task("generate_image", args=[job_id])


async def poll_cycle():
    """
    One poll cycle:
      1. SELECT + lock undispatched outbox rows (FOR UPDATE SKIP LOCKED)
      2. Send each to Celery
      3. Mark dispatched in the same session
      4. COMMIT — transaction ownership stays in the relay, not the repo

    At-least-once delivery guarantee:
      If the relay crashes after send_task() but before COMMIT,
      the same row will be picked up again on the next cycle and
      re-sent to Celery. start_processing() + row state validation
      on the worker side makes duplicate delivery safe.
    """
    async with AsyncSessionLocal() as session:
        repo = OutboxRepository(session)
        rows = await repo.poll_unpublished(limit=20)

        for row in rows:
            dispatch_row(row)          # publish first — crash here → safe duplicate, not a lost job
            repo.mark_dispatched(row)  # mutate in-memory; no DB write yet

        await session.commit()         # single COMMIT for the whole batch


async def run():
    settings = get_settings()
    interval = settings.outbox_poll_interval_ms / 1000

    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    logger.info("Outbox relay started")
    while _running:
        try:
            await poll_cycle()
        except Exception:
            logger.exception("Error during outbox poll cycle")
        await asyncio.sleep(interval)
    logger.info("Outbox relay stopped")


if __name__ == "__main__":
    asyncio.run(run())