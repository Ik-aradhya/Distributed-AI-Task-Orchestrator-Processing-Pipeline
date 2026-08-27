# app/schemas/job.py
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.job import JobStatus


class JobCreateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000)


class JobCreateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: uuid.UUID
    status: JobStatus


class JobStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: uuid.UUID
    status: JobStatus
    result_url: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime