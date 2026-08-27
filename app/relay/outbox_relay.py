# app/relay/outbox_relay.py
import time
import signal
import logging
from app.core.db import get_session
from app.core.config import get_settings
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

def run():
    settings = get_settings()
    interval = settings.outbox_poll_interval_ms / 1000

    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    logger.info("Outbox relay started")
    while _running:
        with get_session() as session:
            repo = OutboxRepository(session)
            for row in repo.poll_unpublished(limit=20):
                dispatch_row(row)          # publish first
                repo.mark_dispatched(row)  # crash here -> safe duplicate publish, not a lost job
        time.sleep(interval)
    logger.info("Outbox relay stopped")

if __name__ == "__main__":
    run()