# app/providers/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class ProviderResult:
    image_url: str
    raw_response: dict

class ProviderError(Exception):
    """Raised for any failure calling the image generation provider."""
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable

class ImageProvider(ABC):
    @abstractmethod
    async def generate(self, prompt: str, idempotency_key: str) -> ProviderResult:
        # idempotency_key is the stable job_id.
        # Every provider implementation MUST accept and forward this key
        # so the external API can deduplicate requests across crash/retry cycles.
        ...