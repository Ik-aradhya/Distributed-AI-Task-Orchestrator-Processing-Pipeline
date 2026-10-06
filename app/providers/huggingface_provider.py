"""
Hugging Face Inference Image Generation Provider.

Uses the Hugging Face hosted inference API / router (via AsyncInferenceClient)
to generate images using open-source models like FLUX.1-schnell or Qwen-Image.
"""
from __future__ import annotations

import os
import pathlib
from PIL import Image
from huggingface_hub import AsyncInferenceClient

from app.core.config import get_settings
from app.core.logging import get_logger
from app.providers.base import ImageProvider, ProviderError, ProviderResult

logger = get_logger("huggingface_provider")

_OUTPUT_DIR = pathlib.Path(os.getenv("IMAGE_OUTPUT_DIR", "/tmp/generated_images"))
_DEFAULT_MODEL = os.getenv("HF_MODEL", "black-forest-labs/FLUX.1-schnell")


class HuggingFaceProvider(ImageProvider):
    """Wraps Hugging Face serverless / router image generation."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        settings = get_settings()
        self._api_key = api_key or settings.provider_api_key.get_secret_value()
        self._model = model or getattr(settings, "hf_model", _DEFAULT_MODEL)
        self._timeout = timeout
        self._client = AsyncInferenceClient(api_key=self._api_key, timeout=self._timeout)
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    async def generate(self, prompt: str, idempotency_key: str) -> ProviderResult:
        logger.info(
            "huggingface_image_request_start",
            model=self._model,
            prompt_len=len(prompt),
            idempotency_key=idempotency_key,
        )

        try:
            image: Image.Image = await self._client.text_to_image(
                prompt,
                model=self._model,
            )
        except Exception as exc:
            err_str = str(exc)
            logger.warning("huggingface_image_error", error=err_str)
            # Detect rate-limit or transient model loading
            if (
                "429" in err_str
                or "rate limit" in err_str.lower()
                or "503" in err_str
                or "loading" in err_str.lower()
                or "timeout" in err_str.lower()
            ):
                raise ProviderError(f"Hugging Face transient error: {err_str}", retryable=True) from exc
            raise ProviderError(f"Hugging Face error: {err_str}", retryable=False) from exc

        filename = f"{idempotency_key}.png"
        out_path = _OUTPUT_DIR / filename
        try:
            image.save(out_path, format="PNG")
        except Exception as exc:
            logger.error("huggingface_image_save_failed", error=str(exc))
            raise ProviderError(f"Failed to save generated image: {exc}", retryable=False) from exc

        image_url = out_path.as_uri()
        logger.info(
            "huggingface_image_saved",
            path=str(out_path),
            url=image_url,
            width=image.width,
            height=image.height,
        )

        return ProviderResult(
            image_url=image_url,
            raw_response={
                "model": self._model,
                "width": image.width,
                "height": image.height,
                "format": "PNG",
            },
        )

    async def close(self) -> None:
        pass
