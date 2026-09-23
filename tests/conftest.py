"""
conftest.py – shared fixtures for the integration test suite.

PostgreSQL: Live PostgreSQL 16 on localhost:5432
Redis: High-performance in-memory async Redis server (fakeredis.aioredis)
"""

import asyncio
import hashlib
import os
import uuid
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://orchestrator:orchestrator@localhost:5432/orchestrator",
)

os.environ["DATABASE_URL"] = TEST_DB_URL
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("PROVIDER_API_KEY", "test-key")
os.environ.setdefault("PROVIDER_BASE_URL", "http://fake-provider")

from app.core.config import get_settings  # noqa: E402
from app.core.db import AsyncSessionLocal, Base, engine as app_engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models.api_key import ApiKey  # noqa: E402
from app.providers.base import ProviderResult  # noqa: E402

_fake_redis_server = fakeredis.FakeServer()


from sqlalchemy.pool import NullPool


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture()
async def test_engine():
    engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    import app.core.db as db_module
    db_module.engine = engine
    db_module.AsyncSessionLocal = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean_database(test_engine):
    """Clean all tables before each test to guarantee complete isolation."""
    async with test_engine.begin() as conn:
        await conn.execute(text("TRUNCATE TABLE outbox, jobs, api_keys CASCADE;"))
    yield
    async with test_engine.begin() as conn:
        await conn.execute(text("TRUNCATE TABLE outbox, jobs, api_keys CASCADE;"))


@pytest_asyncio.fixture()
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    async_session = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    async with async_session() as session:
        yield session


@pytest_asyncio.fixture()
async def redis_client(monkeypatch):
    """Provide async Redis client and patch get_redis across all app modules."""
    client = fakeredis.aioredis.FakeRedis(server=_fake_redis_server, decode_responses=True)
    await client.flushall()

    import app.core.redis_client as rc_module
    import app.api.dependencies as deps_module
    import app.api.routes.jobs as jobs_module
    import app.workers.tasks as tasks_module

    rc_module._redis_client = client
    monkeypatch.setattr(rc_module, "get_redis", lambda: client)
    monkeypatch.setattr(deps_module, "get_redis", lambda: client)
    monkeypatch.setattr(jobs_module, "get_redis", lambda: client)
    monkeypatch.setattr(tasks_module, "get_redis", lambda: client)

    yield client
    await client.flushall()
    await client.aclose()
    rc_module._redis_client = None


RAW_KEY = "integration-test-key-1234"


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


@pytest_asyncio.fixture()
async def api_key(db_session: AsyncSession):
    key_id = uuid.uuid4()
    row = ApiKey(
        id=key_id,
        key_hash=_hash(RAW_KEY),
        name="test-key",
        is_active=True,
    )
    db_session.add(row)
    await db_session.commit()
    return row, RAW_KEY


@pytest_asyncio.fixture()
async def app_client(db_session: AsyncSession, redis_client, api_key):
    from app.core.db import get_db_session
    from app.core.redis_client import get_redis

    async def _override_db():
        yield db_session

    def _override_redis():
        return redis_client

    app.dependency_overrides[get_db_session] = _override_db
    app.dependency_overrides[get_redis] = _override_redis

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client

    app.dependency_overrides.clear()


@pytest.fixture()
def make_provider_mock(monkeypatch):
    import app.workers.tasks as tasks_module

    def factory(
        *,
        return_value: ProviderResult | None = None,
        side_effect=None,
    ):
        mock = AsyncMock()
        if side_effect is not None:
            mock.side_effect = side_effect
        else:
            mock.return_value = return_value or ProviderResult(
                image_url="https://cdn.example.com/img.png",
                raw_response={"url": "https://cdn.example.com/img.png"},
            )
        provider = MagicMock()
        provider.generate = mock
        monkeypatch.setattr(tasks_module, "_provider", provider)
        return provider

    return factory
