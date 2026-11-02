"""Shared worker liveness. A heartbeat is not a claim that a job succeeded."""

import logging

from app.common.time import utcnow
from app.database import SessionLocal
from app.models import WorkerHeartbeat

logger = logging.getLogger("kumbh_setu.worker")

REQUIRED_WORKERS = ("scan", "notifications", "cleanup", "retention")


def record_heartbeat(worker_name: str, *, status: str = "ok") -> None:
    if worker_name not in REQUIRED_WORKERS:
        raise ValueError(f"unknown worker {worker_name}")
    if status not in {"ok", "degraded"}:
        raise ValueError("worker status must be ok or degraded")
    now = utcnow()
    with SessionLocal() as db:
        row = db.get(WorkerHeartbeat, worker_name)
        if row is None:
            db.add(WorkerHeartbeat(worker_name=worker_name, heartbeat_at=now, status=status))
        else:
            row.heartbeat_at = now
            row.status = status
        db.commit()


def beat(worker_name: str, *, status: str = "ok") -> None:
    """Best-effort heartbeat. A database outage must not kill the worker loop."""
    try:
        record_heartbeat(worker_name, status=status)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Worker heartbeat failed", extra={"worker": worker_name, "error": type(exc).__name__})
