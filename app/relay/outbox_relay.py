# app/relay/outbox_relay.py
import asyncio
import signal

from app.core.config import get_settings
from app.core.db import AsyncSessionLocal
from app.core.logging import configure_logging, get_logger
from app.repositories.outbox_repository import OutboxRepository
from app.workers.celery_app import celery_app

logger = get_logger("outbox_relay")
_running = True


def _handle_shutdown(signum, frame):
    global _running
    logger.info("shutdown_signal_received", signal=signum)
    _running = False


def dispatch_row(row):
    job_id = row.payload["job_id"]
    celery_app.send_task("generate_image", args=[job_id])
    logger.debug("outbox_event_dispatched_to_celery", job_id=job_id, event_type=row.event_type)


async def poll_cycle():
    """
    One poll cycle:
      1. SELECT + lock undispatched outbox rows (FOR UPDATE SKIP LOCKED)
      2. Send each to Celery
      3. Mark dispatched in the same session
      4. COMMIT — transaction ownership stays in the relay, not the repo
    """
    async with AsyncSessionLocal() as session:
        repo = OutboxRepository(session)
        rows = await repo.poll_unpublished(limit=20)

        if rows:
            logger.info("outbox_batch_polled", count=len(rows))

        for row in rows:
            dispatch_row(row)
            repo.mark_dispatched(row)

        if rows:
            await session.commit()
            logger.info("outbox_batch_committed", count=len(rows))


async def run():
    configure_logging()
    settings = get_settings()
    interval = settings.outbox_poll_interval_ms / 1000

    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    logger.info("outbox_relay_started", poll_interval_ms=settings.outbox_poll_interval_ms)
    while _running:
        try:
            await poll_cycle()
        except Exception as e:
            logger.exception("outbox_poll_cycle_error", error=str(e))
        await asyncio.sleep(interval)
    logger.info("outbox_relay_stopped")


if __name__ == "__main__":
    asyncio.run(run())