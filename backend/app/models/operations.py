from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class WorkerHeartbeat(Base):
    """Liveness row written by a supervised worker. Absence or age means the worker is not ready."""

    __tablename__ = "worker_heartbeats"

    worker_name: Mapped[str] = mapped_column(String(32), primary_key=True)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
