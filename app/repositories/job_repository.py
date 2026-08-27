# app/repositories/job_repository.py
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job, JobStatus


class JobRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    def create(self, job_id: uuid.UUID, api_key_id: uuid.UUID, prompt: str) -> Job:
        job = Job(id=job_id, api_key_id=api_key_id, prompt=prompt, status=JobStatus.PENDING)
        self.session.add(job)
        return job

    async def get_for_update(self, job_id: uuid.UUID) -> Job | None:
        stmt = select(Job).where(Job.id == job_id).with_for_update()
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    def update_status(self, job: Job, new_status: JobStatus, **fields) -> Job:
        job.status = new_status
        for k, v in fields.items():
            setattr(job, k, v)
        return job

    async def get_by_id(self, job_id: uuid.UUID) -> Job | None:
        stmt = select(Job).where(Job.id == job_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()