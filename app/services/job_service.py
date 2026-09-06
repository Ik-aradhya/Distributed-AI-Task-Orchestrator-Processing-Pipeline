# app/services/job_service.py
import uuid
from app.repositories.job_repository import JobRepository
from app.repositories.outbox_repository import OutboxRepository
from app.services.exceptions import JobNotFound, InvalidTransition

VALID_TRANSITIONS = {
    "PENDING": {"QUEUED"},
    "QUEUED": {"PROCESSING"},
    "PROCESSING": {"COMPLETED", "FAILED"},
    "FAILED": {"RETRYING"},
    "RETRYING": {"QUEUED"},
    "COMPLETED": set(),
}

class JobService:
    def __init__(self, job_repo: JobRepository, outbox_repo: OutboxRepository, max_retries: int):
        self.job_repo = job_repo
        self.outbox_repo = outbox_repo
        self.max_retries = max_retries

    def _assert_transition(self, current: str, target: str):
        if target not in VALID_TRANSITIONS.get(current, set()):
            raise InvalidTransition(current, target)

    async def submit(self, api_key_id: uuid.UUID, prompt: str):
        job_id = uuid.uuid4()
        job = self.job_repo.create(job_id, api_key_id, prompt)
        # same transaction, same session -> atomic with the job insert (outbox pattern)
        self.outbox_repo.create(
            job_id=job_id,
            event_type="JOB_SUBMITTED",
            payload={"job_id": str(job_id), "prompt": prompt},
        )
        return job

    async def start_processing(self, job_id: uuid.UUID):
        job = await self.job_repo.get_for_update(job_id)
        if job is None:
            raise JobNotFound(job_id)
        self._assert_transition(job.status, "PROCESSING")
        return self.job_repo.update_status(job, "PROCESSING")

    async def complete(self, job_id: uuid.UUID, result_url: str):
        job = await self.job_repo.get_for_update(job_id)
        if job is None:
            raise JobNotFound(job_id)
        self._assert_transition(job.status, "COMPLETED")
        return self.job_repo.update_status(job, "COMPLETED", result_url=result_url)

    async def fail(self, job_id: uuid.UUID, error_message: str, retryable: bool):
        job = await self.job_repo.get_for_update(job_id)
        if job is None:
            raise JobNotFound(job_id)
        self._assert_transition(job.status, "FAILED")
        self.job_repo.update_status(job, "FAILED", error_message=error_message)

        if retryable and job.retry_count < self.max_retries:
            self._assert_transition("FAILED", "RETRYING")
            return self.job_repo.update_status(job, "RETRYING", retry_count=job.retry_count + 1)
        return job  # terminal FAILED, retries exhausted or not retryable