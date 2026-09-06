# app/providers/hosted_provider.py
import httpx
from app.providers.base import ImageProvider, ProviderResult, ProviderError
from app.core.config import get_settings

class HostedProvider(ImageProvider):
    def __init__(self, client: httpx.AsyncClient | None = None, timeout: float = 60.0):
        settings = get_settings()
        self.base_url = settings.provider_base_url
        self.api_key = settings.provider_api_key.get_secret_value()
        self.timeout = timeout
        self.client = client or httpx.AsyncClient(timeout=self.timeout)

    async def generate(self, prompt: str) -> ProviderResult:
        try:
            response = await self.client.post(
                f"{self.base_url}/generate",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"prompt": prompt},
            )
        except httpx.TimeoutException as e:
            raise ProviderError(f"Provider timed out: {e}", retryable=True)
        except httpx.RequestError as e:
            raise ProviderError(f"Provider request failed: {e}", retryable=True)

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
            raise ProviderError(f"Failed to parse provider response JSON: {e}", retryable=False)

        return ProviderResult(image_url=data["url"], raw_response=data)

    async def close(self):
        if not self.client.is_closed:
            await self.client.aclose()