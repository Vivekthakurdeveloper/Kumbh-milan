import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import install_error_handlers
from app.api.health import router as health_router
from app.api.middleware import RequestGuardMiddleware
from app.api.v1.router import api_router
from app.config import get_settings
from app.database import engine
from app.observability import configure_logging

DESCRIPTION = """
Case management for missing persons, found persons, lost items, and found items at Haridwar Kumbh Mela 2027.

All `/api/v1` endpoints except login and refresh require `Authorization: Bearer <access token>`.
Visibility is scoped by role and station; ids outside your scope return 404.
Errors use `{"error": {"code", "message", "request_id", "details"}}`.
"""


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    logging.getLogger("kumbh_setu").info("API starting", extra={"env": settings.app_env})
    yield
    logging.getLogger("kumbh_setu").info("API shutting down; closing database pool")
    engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description=DESCRIPTION,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
        lifespan=lifespan,
    )
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
            expose_headers=["X-Request-ID", "Idempotent-Replayed", "Retry-After"],
        )
    app.add_middleware(RequestGuardMiddleware, settings=settings)
    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
