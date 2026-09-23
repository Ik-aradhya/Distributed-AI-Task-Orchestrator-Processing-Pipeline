import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.mark.anyio
async def test_openapi_schema_generated():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/openapi.json")
        assert response.status_code == 200
        data = response.json()
        assert data["info"]["title"] == "AI Image Generation Orchestrator API"
        assert "/jobs" in data["paths"]
        assert "/jobs/{job_id}" in data["paths"]
        assert "/jobs/{job_id}/stream" in data["paths"]
        assert "/health" in data["paths"]
        # Verify schema details
        assert "post" in data["paths"]["/jobs"]
        assert "get" in data["paths"]["/jobs/{job_id}"]
        assert "get" in data["paths"]["/jobs/{job_id}/stream"]
