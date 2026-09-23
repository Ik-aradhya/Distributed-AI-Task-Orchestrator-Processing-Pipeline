"""
test_api_key_auth.py

Scenario: API-key authorization
  - Missing X-API-Key header  → 422 (FastAPI request validation)
  - Wrong key value           → 401 "Invalid API key"
  - Valid key                 → 202
"""

import pytest


@pytest.mark.asyncio
async def test_missing_api_key_returns_422(app_client):
    """FastAPI should reject the request when the required header is absent."""
    resp = await app_client.post("/jobs", json={"prompt": "no auth header"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_invalid_api_key_returns_401(app_client):
    """An unknown key must be rejected with 401."""
    resp = await app_client.post(
        "/jobs",
        json={"prompt": "bad key test"},
        headers={"X-API-Key": "totally-invalid-key-xyz"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid API key"


@pytest.mark.asyncio
async def test_valid_api_key_returns_202(app_client, api_key):
    """A correct API key must allow the request through."""
    _, raw_key = api_key
    resp = await app_client.post(
        "/jobs",
        json={"prompt": "auth test prompt"},
        headers={"X-API-Key": raw_key},
    )
    assert resp.status_code == 202


@pytest.mark.asyncio
async def test_get_job_with_wrong_key_returns_401(app_client, api_key):
    """GET /jobs/{id} with the wrong API key must also return 401."""
    _, raw_key = api_key

    # Submit with valid key to get a real job_id
    resp = await app_client.post(
        "/jobs",
        json={"prompt": "auth isolation test"},
        headers={"X-API-Key": raw_key},
    )
    job_id = resp.json()["job_id"]

    # Attempt to read it with a wrong key
    resp = await app_client.get(
        f"/jobs/{job_id}",
        headers={"X-API-Key": "wrong-key"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_job_with_different_owner_key_returns_404(
    app_client, api_key, db_session
):
    """
    A valid key that belongs to a *different* owner must get 404 for another
    owner's job (ownership isolation).
    """
    import hashlib
    import uuid
    from app.models.api_key import ApiKey

    _, raw_key = api_key

    # Submit a job with the primary key
    resp = await app_client.post(
        "/jobs",
        json={"prompt": "owner isolation test"},
        headers={"X-API-Key": raw_key},
    )
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    # Create a second API key in the DB
    other_raw = "other-owner-key-9876"
    other_row = ApiKey(
        id=uuid.uuid4(),
        key_hash=hashlib.sha256(other_raw.encode()).hexdigest(),
        name="other-owner",
        is_active=True,
    )
    db_session.add(other_row)
    await db_session.commit()

    # Reading the first owner's job with the second key must return 404
    resp = await app_client.get(
        f"/jobs/{job_id}",
        headers={"X-API-Key": other_raw},
    )
    assert resp.status_code == 404
