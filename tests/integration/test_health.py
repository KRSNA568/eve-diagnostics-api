from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from eve.core.config import Settings
from eve.main import create_app


async def test_liveness_probe_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health/live/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_probe_reports_healthy_dependencies(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health/ready/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}


async def test_readiness_probe_returns_503_when_dependencies_are_unreachable(
    settings: Settings,
) -> None:
    unreachable = settings.model_copy(
        update={
            "database_url": "postgresql+psycopg://eve:eve@127.0.0.1:1/eve",
            "redis_url": "redis://127.0.0.1:1/0",
        }
    )
    app = create_app(unreachable)

    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        response = await client.get("/api/v1/health/ready/")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "checks": {"database": "unavailable", "redis": "unavailable"},
    }
