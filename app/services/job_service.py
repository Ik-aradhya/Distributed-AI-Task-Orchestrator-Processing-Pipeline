# app/services/job_service.py

import uuid

from app.models.job import JobStatus
from app.repositories.job_repository import JobRepository
from app.repositories.outbox_repository import OutboxRepository
from app.services.exceptions import JobNotFound, InvalidTransition


VALID_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.PENDING: {JobStatus.QUEUED},
    JobStatus.QUEUED: {JobStatus.PROCESSING},
    JobStatus.PROCESSING: {
        JobStatus.COMPLETED,
        JobStatus.FAILED,
    },
    JobStatus.FAILED: {JobStatus.RETRYING},
    JobStatus.RETRYING: {JobStatus.QUEUED},
    JobStatus.COMPLETED: set(),
}


class JobService:
    def __init__(
        self,
        job_repo: JobRepository,
        outbox_repo: OutboxRepository,
        max_retries: int,
    ):
        self.job_repo = job_repo
        self.outbox_repo = outbox_repo
        self.max_retries = max_retries

    def _assert_transition(
        self,
        current: JobStatus,
        target: JobStatus,
    ) -> None:
        if target not in VALID_TRANSITIONS.get(current, set()):
            raise InvalidTransition(current, target)

    def submit(
        self,
        api_key_id: uuid.UUID,
        prompt: str,
    ):
        job_id = uuid.uuid4()

        job = self.job_repo.create(
            job_id,
            api_key_id,
            prompt,
        )

        self._assert_transition(
            JobStatus.PENDING,
            JobStatus.QUEUED,
        )

        self.job_repo.update_status(
            job,
            JobStatus.QUEUED,
        )

        self.outbox_repo.create(
            job_id=job_id,
            event_type="JOB_SUBMITTED",
            payload={
                "job_id": str(job_id),
                "prompt": prompt,
            },
        )

        return job

    async def start_processing(
        self,
        job_id: uuid.UUID,
    ):
        job = await self.job_repo.get_for_update(job_id)

        if job is None:
            raise JobNotFound(job_id)

        # Crash recovery: a previous worker died mid-task leaving the job in PROCESSING.
        # get_for_update() row-lock guarantees only one worker reaches this branch at a time,
        # so we can safely re-enter without a state write — the status is already correct.
        if job.status == JobStatus.PROCESSING:
            return job

        # Retry redelivery: advance RETRYING → QUEUED first, then fall through to
        # the normal QUEUED → PROCESSING transition below.
        if job.status == JobStatus.RETRYING:
            self._assert_transition(JobStatus.RETRYING, JobStatus.QUEUED)
            self.job_repo.update_status(job, JobStatus.QUEUED)

        self._assert_transition(job.status, JobStatus.PROCESSING)
        return self.job_repo.update_status(job, JobStatus.PROCESSING)

    async def complete(
        self,
        job_id: uuid.UUID,
        result_url: str,
    ):
        job = await self.job_repo.get_for_update(job_id)

        if job is None:
            raise JobNotFound(job_id)

        self._assert_transition(
            job.status,
            JobStatus.COMPLETED,
        )

        return self.job_repo.update_status(
            job,
            JobStatus.COMPLETED,
            result_url=result_url,
        )

    async def fail(
        self,
        job_id: uuid.UUID,
        error_message: str,
        retryable: bool,
    ):
        job = await self.job_repo.get_for_update(job_id)

        if job is None:
            raise JobNotFound(job_id)

        self._assert_transition(
            job.status,
            JobStatus.FAILED,
        )

        self.job_repo.update_status(
            job,
            JobStatus.FAILED,
            error_message=error_message,
        )

        if retryable and job.retry_count < self.max_retries:
            # Celery will re-deliver the task; start_processing() will advance
            # RETRYING → QUEUED → PROCESSING on the next attempt.
            self._assert_transition(
                JobStatus.FAILED,
                JobStatus.RETRYING,
            )

            return self.job_repo.update_status(
                job,
                JobStatus.RETRYING,
                retry_count=job.retry_count + 1,
            )

        return job  # terminal FAILED — retries exhausted or non-retryable