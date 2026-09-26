"""Guards on the published API documentation."""

from httpx import AsyncClient


async def test_every_operation_is_documented(client: AsyncClient) -> None:
    spec = (await client.get("/openapi.json")).json()

    operations = [
        (method.upper(), path, operation)
        for path, item in spec["paths"].items()
        for method, operation in item.items()
    ]
    undocumented = [
        f"{m} {p}" for m, p, op in operations if not op.get("summary") or not op.get("tags")
    ]
    assert undocumented == []
    assert {tag["name"] for tag in spec["tags"]} >= {op["tags"][0] for _, _, op in operations}


async def test_protected_operations_declare_bearer_auth(client: AsyncClient) -> None:
    spec = (await client.get("/openapi.json")).json()

    create_booking = spec["paths"]["/api/v1/bookings/"]["post"]
    assert create_booking["security"] == [{"HTTPBearer": []}]


async def test_errors_are_documented_with_the_shared_envelope(client: AsyncClient) -> None:
    spec = (await client.get("/openapi.json")).json()

    pay = spec["paths"]["/api/v1/payments/"]["post"]["responses"]
    assert pay["409"]["content"]["application/json"]["schema"]["$ref"].endswith("ErrorResponse")
    assert pay["422"]["content"]["application/json"]["schema"]["$ref"].endswith("ErrorResponse")


async def test_swagger_ui_is_served(client: AsyncClient) -> None:
    response = await client.get("/docs")

    assert response.status_code == 200
    assert "swagger-ui" in response.text
