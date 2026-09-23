# app/schemas/job.py
import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field

from app.models.job import JobStatus


class JobCreateRequest(BaseModel):
    prompt: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Text prompt describing the desired image generation.",
        examples=["A futuristic cyberpunk cityscape at sunset with neon reflections in the rain, 8k resolution, photorealistic"],
    )


class JobCreateResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "status": "QUEUED",
            }
        },
    )

    job_id: uuid.UUID = Field(
        ...,
        description="Unique UUID identifier for the created image generation job.",
    )
    status: JobStatus = Field(
        ...,
        description="Initial status of the submitted job.",
        examples=["QUEUED"],
    )


class JobStatusResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "status": "COMPLETED",
                "result_url": "https://storage.googleapis.com/generated-images/a1b2c3d4.png",
                "error_message": None,
                "created_at": "2026-09-23T12:00:00Z",
                "updated_at": "2026-09-23T12:00:05Z",
            }
        },
    )

    job_id: uuid.UUID = Field(
        ...,
        description="Unique UUID identifier of the job.",
    )
    status: JobStatus = Field(
        ...,
        description="Current processing lifecycle status of the job.",
        examples=["QUEUED", "PROCESSING", "COMPLETED", "FAILED"],
    )
    created_at: datetime = Field(
        ...,
        description="UTC timestamp when the job was submitted.",
    )
    updated_at: datetime = Field(
        ...,
        description="UTC timestamp of the latest status modification.",
    )
    result_url: str | None = Field(
        default=None,
        description="URL of the generated image asset once completed (null if pending or failed).",
    )
    error_message: str | None = Field(
        default=None,
        description="Human-readable failure reason if the job failed permanently (null if succeeded or pending).",
    )


class JobStatusStreamEvent(BaseModel):
    """Schema representing the JSON payload streamed inside each Server-Sent Event (SSE)."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "status": "COMPLETED",
                "result_url": "https://storage.googleapis.com/generated-images/a1b2c3d4.png",
            }
        }
    )

    job_id: str = Field(..., description="Job identifier")
    status: str = Field(..., description="Job status update (QUEUED, PROCESSING, COMPLETED, FAILED)")
    result_url: str | None = Field(default=None, description="Image URL if completed")
    error: str | None = Field(default=None, description="Error message if failed")


class ErrorResponse(BaseModel):
    """Standardized API error response format."""

    detail: str = Field(..., description="Human-readable description of the error.")

    
