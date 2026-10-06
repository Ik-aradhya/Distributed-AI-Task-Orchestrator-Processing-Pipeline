import hashlib
import hmac

from fastapi import Depends, Header, HTTPException, status

from app.core.db import AsyncSessionLocal
from app.core.redis_client import get_redis
from app.core.config import get_settings
from app.core.logging import get_logger
from app.repositories.api_key_repository import ApiKeyRepository
from app.services.rate_limit_service import RateLimitService

logger = get_logger("api_auth")


def hash_api_key(raw_key: str) -> str:
    settings = get_settings()
    if settings.api_key_hash_pepper is not None:
        pepper = settings.api_key_hash_pepper.get_secret_value().encode()
        return hmac.new(pepper, raw_key.encode(), hashlib.sha256).hexdigest()
    return hashlib.sha256(raw_key.encode()).hexdigest()


def legacy_hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


async def get_current_api_key(
    x_api_key: str = Header(..., alias="X-API-Key"),
):
    key_hashes = [hash_api_key(x_api_key)]
    legacy_hash = legacy_hash_api_key(x_api_key)
    if legacy_hash not in key_hashes:
        key_hashes.append(legacy_hash)

    async with AsyncSessionLocal() as session:
        api_key = None
        for key_hash in key_hashes:
            api_key = await ApiKeyRepository(session).get_by_hash(key_hash)
            if api_key is not None:
                break

        if api_key is None:
            logger.warning("api_key_auth_failed")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
            )
        return api_key


async def enforce_rate_limit(api_key=Depends(get_current_api_key)):
    settings = get_settings()
    limiter = RateLimitService(get_redis(), settings.rate_limit_requests_per_minute)
    allowed = await limiter.check_and_increment(str(api_key.id))
    if not allowed:
        logger.warning("rate_limit_exceeded", api_key_id=str(api_key.id))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
        )
    return api_key
