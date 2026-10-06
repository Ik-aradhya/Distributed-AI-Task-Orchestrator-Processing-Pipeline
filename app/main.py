from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import health, jobs
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.redis_client import close_redis


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    yield
    await close_redis()


tags_metadata = [
    {
        "name": "Jobs",
        "description": "Endpoints to submit image generation jobs, check their status, and stream real-time events via SSE.",
    },
    {
        "name": "Health",
        "description": "Infrastructure readiness and liveness health checks.",
    },
]

settings = get_settings()
docs_url = "/docs" if settings.api_docs_enabled else None
redoc_url = "/redoc" if settings.api_docs_enabled else None
openapi_url = "/openapi.json" if settings.api_docs_enabled else None

app = FastAPI(
    title="AI Image Generation Orchestrator API",
    description="""
## Distributed AI Task Orchestrator & Processing Pipeline

A distributed, production-grade image generation orchestrator providing:
- **Transactional Outbox Pattern** guaranteeing zero message loss between DB & Celery.
- **Asynchronous Task Processing** via Celery workers with late ACKs and crash-safety.
- **Server-Sent Events (SSE)** for real-time lifecycle event broadcasting.
- **API Key Authentication** & sliding-window rate limiting.
- **Idempotent External Provider Integration** with categorized retry policies.

### Authentication
All `/jobs` endpoints require an active API key passed via the **`X-API-Key`** header.
""",
    version="1.0.0",
    openapi_tags=tags_metadata,
    lifespan=lifespan,
    docs_url=docs_url,
    redoc_url=redoc_url,
    openapi_url=openapi_url,
)

app.include_router(jobs.router)
app.include_router(health.router)
