import hashlib
import hmac

import httpx
import pytest
from pydantic import ValidationError

from app.api.dependencies import hash_api_key
from app.core.config import Settings, get_settings
from app.providers.base import ProviderError
from app.providers.hosted_provider import HostedProvider
from app.schemas.job import JobCreateRequest


def test_api_key_hash_uses_configured_pepper(monkeypatch):
    monkeypatch.setenv("API_KEY_HASH_PEPPER", "pepper-secret")
    get_settings.cache_clear()

    try:
        expected = hmac.new(
            b"pepper-secret",
            b"raw-api-key",
            hashlib.sha256,
        ).hexdigest()
        assert hash_api_key("raw-api-key") == expected
    finally:
        get_settings.cache_clear()


def test_prompt_rejects_whitespace_only_input():
    with pytest.raises(ValidationError):
        JobCreateRequest(prompt="   ")


def test_provider_base_url_requires_https_by_default():
    with pytest.raises(ValidationError):
        Settings(
            database_url="postgresql+asyncpg://user:pass@localhost:5432/app",
            redis_url="redis://localhost:6379/0",
            provider_api_key="provider-key",
            provider_allow_insecure_http=False,
            provider_base_url="http://provider.example",
        )


@pytest.mark.asyncio
async def test_provider_request_error_is_sanitized():
    class FailingClient:
        is_closed = False

        async def post(self, *args, **kwargs):
            request = httpx.Request("POST", "https://secret-provider.example/generate")
            raise httpx.RequestError("connect failed for https://secret-provider.example", request=request)

        async def aclose(self):
            self.is_closed = True

    provider = HostedProvider(client=FailingClient())

    with pytest.raises(ProviderError) as exc_info:
        await provider.generate("prompt", idempotency_key="job-id")

    assert str(exc_info.value) == "Provider request failed"
    assert "secret-provider" not in str(exc_info.value)
