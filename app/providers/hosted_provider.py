# app/providers/hosted_provider.py
import httpx
from app.providers.base import ImageProvider, ProviderResult, ProviderError
from app.core.config import get_settings

class HostedProvider(ImageProvider):
    def __init__(self):
        settings = get_settings()
        self.base_url = settings.provider_base_url
        self.api_key = settings.provider_api_key.get_secret_value()
        self.client = httpx.Client(timeout=30.0)

    def generate(self, prompt: str) -> ProviderResult:
        try:
            response = self.client.post(
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

        data = response.json()
        return ProviderResult(image_url=data["url"], raw_response=data)