from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import health, jobs
from app.core.logging import configure_logging
from app.core.redis_client import close_redis


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    yield
    await close_redis()


app = FastAPI(
    title="AI Image Generation Orchestrator",
    lifespan=lifespan,
)

app.include_router(jobs.router)
app.include_router(health.router)