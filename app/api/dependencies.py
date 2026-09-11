# app/api/dependencies.py
import hashlib

from fastapi import Depends, Header, HTTPException

from app.core.db import AsyncSessionLocal
from app.core.redis_client import get_redis
from app.core.config import get_settings
from app.repositories.api_key_repository import ApiKeyRepository
from app.services.rate_limit_service import RateLimitService


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


async def get_current_api_key(
    x_api_key: str = Header(..., alias="X-API-Key"),
):
    async with AsyncSessionLocal() as session:
        api_key = await ApiKeyRepository(session).get_by_hash(hash_api_key(x_api_key))
        if api_key is None:
            raise HTTPException(status_code=401, detail="Invalid API key")
        return api_key


async def enforce_rate_limit(api_key=Depends(get_current_api_key)):
    settings = get_settings()
    limiter = RateLimitService(get_redis(), settings.rate_limit_requests_per_minute)
    if not await limiter.check_and_increment(str(api_key.id)):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    return api_key