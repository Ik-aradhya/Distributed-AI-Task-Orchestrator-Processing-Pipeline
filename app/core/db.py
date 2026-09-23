from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()

import os
import sys
from sqlalchemy.pool import NullPool

pool_kwargs = {"pool_pre_ping": True}
if "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("TEST_DATABASE_URL"):
    pool_kwargs = {"poolclass": NullPool}

engine = create_async_engine(
    str(settings.database_url),
    **pool_kwargs,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise