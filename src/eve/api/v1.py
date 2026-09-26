from fastapi import APIRouter, status

from eve.api import health
from eve.auth import router as auth
from eve.catalog import router as catalog
from eve.core.errors import ErrorResponse

# Documents the shared error envelope on every route (and replaces FastAPI's default
# 422 schema, which does not match what the API actually returns).
api_router = APIRouter(
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": "Request validation failed",
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "model": ErrorResponse,
            "description": "Unexpected server error",
        },
    }
)
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(catalog.centres_router)
api_router.include_router(catalog.tests_router)
