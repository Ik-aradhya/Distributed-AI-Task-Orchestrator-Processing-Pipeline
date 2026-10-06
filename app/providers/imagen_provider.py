"""
Google Gemini Image Generation provider.

Uses the Gemini generateContent API with IMAGE response modality:

  POST https://generativelanguage.googleapis.com/v1beta/models/
       gemini-3-pro-image:generateContent?key=<API_KEY>

  Request body:
    {
      "contents": [{"parts": [{"text": "<prompt>"}]}],
      "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]}
    }

  Response body (success):
    {
      "candidates": [{
        "content": {
          "parts": [
            {"inlineData": {"mimeType": "image/png", "data": "<base64>"}}
          ]
        }
      }]
    }

Because the API returns raw base64 bytes (not a hosted URL), this
provider saves the image to a well-known local path and returns a
file:// URL.  In production you would swap this out for an upload step
to GCS / S3.
"""

from __future__ import annotations

import base64
import os
import pathlib

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.providers.base import ImageProvider, ProviderError, ProviderResult

logger = get_logger("imagen_provider")

# Where generated images are stored locally (docker volume or local dir)
_OUTPUT_DIR = pathlib.Path(os.getenv("IMAGE_OUTPUT_DIR", "/tmp/generated_images"))

_GEMINI_MODEL = "gemini-3-pro-image"
_GENERATE_PATH = f"/v1beta/models/{_GEMINI_MODEL}:generateContent"


class ImagenProvider(ImageProvider):
    """Wraps Google Gemini image generation via the generateContent API."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        timeout: float = 120.0,
    ) -> None:
        settings = get_settings()
        self._api_key = settings.provider_api_key.get_secret_value()
        self._base_url = settings.provider_base_url  # https://generativelanguage.googleapis.com
        self._timeout = timeout
        self._client = client or httpx.AsyncClient(timeout=self._timeout)
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # ImageProvider interface
    # ------------------------------------------------------------------

    async def generate(self, prompt: str, idempotency_key: str) -> ProviderResult:
        url = f"{self._base_url}{_GENERATE_PATH}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]},
        }
        params = {"key": self._api_key}

        logger.info(
            "gemini_image_request_start",
            model=_GEMINI_MODEL,
            prompt_len=len(prompt),
            idempotency_key=idempotency_key,
        )

        try:
            response = await self._client.post(url, json=payload, params=params)
        except httpx.TimeoutException as exc:
            logger.warning("gemini_image_timeout", error=str(exc))
            raise ProviderError("Gemini image request timed out", retryable=True) from exc
        except httpx.RequestError as exc:
            logger.warning("gemini_image_request_error", error=str(exc))
            raise ProviderError("Gemini image connection error", retryable=True) from exc

        # --- HTTP-level errors ---
        if response.status_code == 429:
            raise ProviderError("Gemini image API rate-limited", retryable=True)
        if response.status_code >= 500:
            raise ProviderError(
                f"Gemini image server error: {response.status_code}", retryable=True
            )
        if response.status_code >= 400:
            try:
                detail = response.json()
            except Exception:
                detail = response.text
            raise ProviderError(
                f"Gemini image rejected request ({response.status_code}): {detail}",
                retryable=False,
            )

        # --- Parse response ---
        try:
            data = response.json()
            candidates = data.get("candidates") or []
            if not candidates:
                raise ProviderError(
                    "Gemini image returned no candidates", retryable=False
                )
            parts = candidates[0].get("content", {}).get("parts", [])
            # Find the first part containing inline image data
            image_part = next(
                (p for p in parts if "inlineData" in p), None
            )
            if not image_part:
                raise ProviderError(
                    "Gemini image response contained no image data", retryable=False
                )
            b64_bytes: str = image_part["inlineData"]["data"]
            mime_type: str = image_part["inlineData"].get("mimeType", "image/png")
        except ProviderError:
            raise
        except Exception as exc:
            logger.warning("gemini_image_parse_error", error=str(exc))
            raise ProviderError(
                "Failed to parse Gemini image response", retryable=False
            ) from exc

        # --- Persist image locally and return a file:// URL ---
        ext = mime_type.split("/")[-1]  # "png", "jpeg", etc.
        filename = f"{idempotency_key}.{ext}"
        out_path = _OUTPUT_DIR / filename
        out_path.write_bytes(base64.b64decode(b64_bytes))

        image_url = out_path.as_uri()  # file:///app/generated_images/<uuid>.png
        logger.info("gemini_image_saved", path=str(out_path), url=image_url)

        return ProviderResult(image_url=image_url, raw_response=data)

    async def close(self) -> None:
        if not self._client.is_closed:
            await self._client.aclose()
