from httpx import AsyncClient


async def test_liveness_probe_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health/live/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
