"""Smoke tests for the runnable Phase 0 API image."""

import httpx
import pytest

from chunkforge.api.main import app


@pytest.mark.anyio
async def test_health_endpoint() -> None:
    """The liveness endpoint should expose its stable typed contract."""

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "chunkforge-api",
        "version": "0.1.0",
    }
