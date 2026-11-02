import hmac
import logging
from datetime import timedelta
from functools import lru_cache

import redis
from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.common.errors import Unauthenticated
from app.common.time import utcnow
from app.config import get_settings
from app.database import engine
from app.database.revision import EXPECTED_SCHEMA_REVISION
from app.observability.metrics import REGISTRY
from app.storage import get_storage
from app.storage.scanner import get_scanner
from app.workers.runtime import REQUIRED_WORKERS

logger = logging.getLogger("kumbh_setu.health")
router = APIRouter(tags=["Operations"])


@lru_cache
def _redis() -> redis.Redis:
    settings = get_settings()
    timeout = settings.redis_timeout_ms / 1000
    return redis.Redis.from_url(settings.redis_url, socket_timeout=timeout, socket_connect_timeout=timeout)


@router.get("/health", summary="Liveness: the process is running")
def health() -> dict:
    return {"status": "ok"}


def _database_checks(checks: dict[str, str]) -> None:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            checks["database"] = "ok"
            try:
                versions = [row[0] for row in conn.execute(text("SELECT version_num FROM alembic_version"))]
            except Exception as exc:  # noqa: BLE001
                logger.warning("Schema readiness check failed", extra={"error": type(exc).__name__})
                checks["schema"] = "incompatible"
                return
            checks["schema"] = "ok" if versions == [EXPECTED_SCHEMA_REVISION] else "incompatible"
    except Exception as exc:  # noqa: BLE001 - report, never raise, from a probe
        logger.warning("Database readiness check failed", extra={"error": type(exc).__name__})
        checks["database"] = "unavailable"
        checks["schema"] = "incompatible"


def _worker_checks(checks: dict[str, str], *, max_age_seconds: int) -> None:
    cutoff = utcnow() - timedelta(seconds=max_age_seconds)
    fresh: set[str] = set()
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT worker_name, status, heartbeat_at FROM worker_heartbeats"))
            for name, status, heartbeat_at in rows:
                if status == "ok" and heartbeat_at is not None and heartbeat_at >= cutoff:
                    fresh.add(name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Worker readiness check failed", extra={"error": type(exc).__name__})
    for name in REQUIRED_WORKERS:
        checks[f"worker_{name}"] = "ok" if name in fresh else "unavailable"


@router.get("/ready", summary="Readiness: required dependencies and schema compatibility")
def ready() -> JSONResponse:
    """
    Required dependencies decide the status code. The malware scanner is reported but does not make the API
    unready: uploads stay quarantined until it returns, and the rest of the system keeps working.
    Responses name the failing check only. They never include connection strings or exception text.
    """
    settings = get_settings()
    checks: dict[str, str] = {}
    _database_checks(checks)
    if settings.workers_required:
        _worker_checks(checks, max_age_seconds=settings.worker_heartbeat_max_age_seconds)
    if settings.s3_endpoint_url or settings.s3_access_key_id:
        try:
            get_storage().check()
            checks["object_storage"] = "ok"
        except Exception as exc:  # noqa: BLE001
            logger.warning("Object storage readiness check failed", extra={"error": type(exc).__name__})
            checks["object_storage"] = "unavailable"
    if settings.rate_limit_backend == "redis" and settings.redis_url:
        try:
            _redis().ping()
            checks["rate_limiter"] = "ok"
        except Exception as exc:  # noqa: BLE001
            logger.warning("Rate limiter readiness check failed", extra={"error": type(exc).__name__})
            checks["rate_limiter"] = "unavailable"
    ok = all(v == "ok" for v in checks.values())
    informational: dict[str, str] = {}
    scanner = get_scanner()
    informational["malware_scanner"] = scanner.health()
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ok" if ok else "degraded", "checks": checks, "informational": informational},
    )


@router.get("/metrics", include_in_schema=False)
def metrics(authorization: str | None = Header(default=None)) -> Response:
    token = get_settings().metrics_token
    if token:
        supplied = (authorization or "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied.encode(), token.encode()):
            raise Unauthenticated("Metrics require the scrape token")
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
