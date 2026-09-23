"""
test_rate_limiting.py

Scenario: Rate limiting
  With a limit of 3 req/min, the 4th request within the same window returns 429.
"""

import pytest


@pytest.mark.asyncio
async def test_rate_limit_blocks_excess_requests(
    app_client, api_key, redis_client, monkeypatch
):
    """
    Override the rate limit to 3 req/min.
    First 3 submissions → 202.
    Fourth submission → 429.
    """
    from app.core import config as config_module

    original_settings = config_module.get_settings()

    class _LowLimitSettings:
        database_url = original_settings.database_url
        redis_url = original_settings.redis_url
        provider_api_key = original_settings.provider_api_key
        provider_base_url = original_settings.provider_base_url
        log_level = original_settings.log_level
        celery_max_retries = original_settings.celery_max_retries
        celery_backoff_base_seconds = original_settings.celery_backoff_base_seconds
        rate_limit_requests_per_minute = 3
        outbox_poll_interval_ms = original_settings.outbox_poll_interval_ms

    import app.api.dependencies as deps_mod
    monkeypatch.setattr(config_module, "get_settings", lambda: _LowLimitSettings())
    monkeypatch.setattr(deps_mod, "get_settings", lambda: _LowLimitSettings())

    _, raw_key = api_key
    headers = {"X-API-Key": raw_key}
    body = {"prompt": "rate limit test"}

    statuses = []
    for _ in range(4):
        resp = await app_client.post("/jobs", json=body, headers=headers)
        statuses.append(resp.status_code)

    assert statuses[:3] == [202, 202, 202], f"First 3 should be 202, got {statuses[:3]}"
    assert statuses[3] == 429, f"4th request should be 429, got {statuses[3]}"


@pytest.mark.asyncio
async def test_rate_limit_resets_in_new_window(
    app_client, api_key, redis_client, monkeypatch
):
    """
    Verify the rate-limit key is window-based: flushing Redis simulates a new
    minute window and the requests are allowed again.
    """
    from app.core import config as config_module

    original_settings = config_module.get_settings()

    class _LowLimitSettings:
        database_url = original_settings.database_url
        redis_url = original_settings.redis_url
        provider_api_key = original_settings.provider_api_key
        provider_base_url = original_settings.provider_base_url
        log_level = original_settings.log_level
        celery_max_retries = original_settings.celery_max_retries
        celery_backoff_base_seconds = original_settings.celery_backoff_base_seconds
        rate_limit_requests_per_minute = 2
        outbox_poll_interval_ms = original_settings.outbox_poll_interval_ms

    import app.api.dependencies as deps_mod
    monkeypatch.setattr(config_module, "get_settings", lambda: _LowLimitSettings())
    monkeypatch.setattr(deps_mod, "get_settings", lambda: _LowLimitSettings())

    _, raw_key = api_key
    headers = {"X-API-Key": raw_key}
    body = {"prompt": "window reset test"}

    # Exhaust the limit
    for _ in range(2):
        await app_client.post("/jobs", json=body, headers=headers)

    resp = await app_client.post("/jobs", json=body, headers=headers)
    assert resp.status_code == 429

    # Simulate new minute by flushing the rate-limit keys
    await redis_client.flushdb()

    resp = await app_client.post("/jobs", json=body, headers=headers)
    assert resp.status_code == 202, "After flush, request should be allowed again"
