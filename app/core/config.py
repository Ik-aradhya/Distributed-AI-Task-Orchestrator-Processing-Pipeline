from functools import lru_cache
from pydantic import PostgresDsn, RedisDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="forbid")

    # Database
    database_url: PostgresDsn

    # Redis — backs broker, pub/sub, and rate limiting
    redis_url: RedisDsn

    # Image generation provider
    provider_api_key: SecretStr
    provider_base_url: str

    # Logging
    log_level: str = "INFO"

    # Celery retry tuning
    celery_max_retries: int = 5
    celery_backoff_base_seconds: int = 2

    # Rate limiting
    rate_limit_requests_per_minute: int = 60

    # Outbox relay
    outbox_poll_interval_ms: int = 200


@lru_cache
def get_settings() -> Settings:
    return Settings()