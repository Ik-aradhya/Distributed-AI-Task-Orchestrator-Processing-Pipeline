from functools import lru_cache
from urllib.parse import urlparse

from pydantic import PostgresDsn, RedisDsn, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: PostgresDsn

    # Redis — backs broker, pub/sub, and rate limiting
    redis_url: RedisDsn

    # Image generation provider
    provider_type: str = "huggingface"
    provider_api_key: SecretStr
    provider_allow_insecure_http: bool = False
    provider_base_url: str = "https://router.huggingface.co"
    hf_model: str = "black-forest-labs/FLUX.1-schnell"

    # API key hashing
    api_key_hash_pepper: SecretStr | None = None

    # Logging
    log_level: str = "INFO"

    # Celery retry tuning
    celery_max_retries: int = 5
    celery_backoff_base_seconds: int = 2

    # Rate limiting
    rate_limit_requests_per_minute: int = 60

    # Outbox relay
    outbox_poll_interval_ms: int = 200

    # API documentation exposure
    api_docs_enabled: bool = False

    @field_validator("provider_base_url")
    @classmethod
    def validate_provider_base_url(cls, value: str, info) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("PROVIDER_BASE_URL must be an absolute HTTP(S) URL")
        allow_insecure = bool(info.data.get("provider_allow_insecure_http", False))
        if parsed.scheme != "https" and not allow_insecure:
            raise ValueError("PROVIDER_BASE_URL must use HTTPS unless PROVIDER_ALLOW_INSECURE_HTTP=true")
        return value.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()
