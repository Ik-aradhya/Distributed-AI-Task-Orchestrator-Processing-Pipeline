import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.providers.base import ImageProvider, ProviderResult, ProviderError

logger = get_logger("hosted_provider")


class HostedProvider(ImageProvider):
    def __init__(self, client: httpx.AsyncClient | None = None, timeout: float = 60.0):
        settings = get_settings()
        self.base_url = settings.provider_base_url
        self.api_key = settings.provider_api_key.get_secret_value()
        self.timeout = timeout
        self.client = client or httpx.AsyncClient(timeout=self.timeout)

    async def generate(self, prompt: str, idempotency_key: str) -> ProviderResult:
        try:
            response = await self.client.post(
                f"{self.base_url}/generate",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    # Stable job_id sent on every attempt (including retries).
                    # If the provider supports deduplication, it will return the
                    # previously generated result instead of creating a new image.
                    "Idempotency-Key": idempotency_key,
                },
                json={"prompt": prompt},
            )
        except httpx.TimeoutException as e:
            logger.warning("provider_timeout", error_type=type(e).__name__)
            raise ProviderError("Provider timed out", retryable=True) from e
        except httpx.RequestError as e:
            logger.warning("provider_request_failed", error_type=type(e).__name__)
            raise ProviderError("Provider request failed", retryable=True) from e

        if response.status_code == 429:
            raise ProviderError("Provider rate limited us", retryable=True)
        if response.status_code >= 500:
            raise ProviderError(f"Provider server error: {response.status_code}", retryable=True)
        if response.status_code >= 400:
            raise ProviderError(f"Provider rejected request: {response.status_code}", retryable=False)

        try:
            data = response.json()
            if not isinstance(data, dict) or "url" not in data or not isinstance(data["url"], str):
                raise ProviderError("Provider response payload missing 'url' string", retryable=False)
        except Exception as e:
            if isinstance(e, ProviderError):
                raise
            logger.warning("provider_response_json_parse_failed", error_type=type(e).__name__)
            raise ProviderError("Failed to parse provider response JSON", retryable=False) from e

        return ProviderResult(image_url=data["url"], raw_response=data)

    async def close(self):
        if not self.client.is_closed:
            await self.client.aclose()
