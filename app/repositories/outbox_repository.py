# app/repositories/outbox_repository.py
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.outbox import Outbox


class OutboxRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    def create(self, job_id: uuid.UUID, event_type: str, payload: dict) -> Outbox:
        row = Outbox(
            job_id=job_id,
            event_type=event_type,
            payload=payload,
            dispatched_at=None,
        )
        self.session.add(row)
        return row

    async def poll_unpublished(self, limit: int = 20) -> list[Outbox]:
        stmt = (
            select(Outbox)
            .where(Outbox.dispatched_at.is_(None))
            .order_by(Outbox.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    def mark_dispatched(self, row: Outbox) -> Outbox:
        row.dispatched_at = datetime.now(timezone.utc)
        return row