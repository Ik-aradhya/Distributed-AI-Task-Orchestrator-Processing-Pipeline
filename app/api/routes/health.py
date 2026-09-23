# app/api/routes/health.py
import time
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_db_session
from app.core.logging import get_logger
from app.core.redis_client import get_redis

logger = get_logger("health_check")
router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    summary="System Health & Readiness Probe",
    description="Performs active ping checks against PostgreSQL and Redis to verify database and broker readiness.",
    status_code=status.HTTP_200_OK,
    responses={
        200: {
            "description": "All infrastructure dependencies are reachable and healthy.",
            "content": {
                "application/json": {
                    "example": {
                        "status": "healthy",
                        "database": {"status": "up", "latency_ms": 1.2},
                        "redis": {"status": "up", "latency_ms": 0.8},
                    }
                }
            },
        },
        503: {
            "description": "One or more infrastructure dependencies failed readiness check.",
        },
    },
)
async def health_check(db: AsyncSession = Depends(get_db_session)):
    checks = {}
    is_healthy = True

    # 1. PostgreSQL check
    db_start = time.perf_counter()
    try:
        await db.execute(text("SELECT 1"))
        db_latency = round((time.perf_counter() - db_start) * 1000, 2)
        checks["database"] = {"status": "up", "latency_ms": db_latency}
    except Exception as e:
        is_healthy = False
        checks["database"] = {"status": "down", "error": str(e)}
        logger.error("health_check_db_failed", error=str(e))

    # 2. Redis check
    redis_start = time.perf_counter()
    try:
        await get_redis().ping()
        redis_latency = round((time.perf_counter() - redis_start) * 1000, 2)
        checks["redis"] = {"status": "up", "latency_ms": redis_latency}
    except Exception as e:
        is_healthy = False
        checks["redis"] = {"status": "down", "error": str(e)}
        logger.error("health_check_redis_failed", error=str(e))

    if not is_healthy:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "unhealthy", "components": checks},
        )

    return {"status": "healthy", "components": checks}