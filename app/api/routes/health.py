# app/api/routes/health.py
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_db_session
from app.core.redis_client import get_redis

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db_session)):
    await db.execute(text("SELECT 1"))
    await get_redis().ping()
    return {"status": "ok"}