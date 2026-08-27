# app/repositories/api_key_repository.py
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_key import ApiKey


class ApiKeyRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_hash(self, key_hash: str) -> ApiKey | None:
        stmt = select(ApiKey).where(
            ApiKey.key_hash == key_hash,
            ApiKey.is_active.is_(True),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()