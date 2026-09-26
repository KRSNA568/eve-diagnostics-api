from importlib.metadata import version

from fastapi import FastAPI

from eve.api.v1 import api_router
from eve.core.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=version("eve"),
        debug=settings.debug,
    )
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
