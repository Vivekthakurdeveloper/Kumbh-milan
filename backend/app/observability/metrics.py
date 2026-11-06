"""Prometheus metrics. Labels never carry personal data: only route templates, statuses, and enum values."""

import logging

from prometheus_client import CollectorRegistry, Counter, Histogram
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector
from sqlalchemy import event
from sqlalchemy.orm import Session

logger = logging.getLogger("kumbh_setu.metrics")

REGISTRY = CollectorRegistry(auto_describe=True)

HTTP_REQUESTS = Counter(
    "kumbh_http_requests_total", "HTTP requests", ["method", "route", "status"], registry=REGISTRY
)
HTTP_LATENCY = Histogram(
    "kumbh_http_request_duration_seconds", "HTTP request latency", ["method", "route"], registry=REGISTRY,
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
AUTH_FAILURES = Counter("kumbh_auth_failures_total", "Failed logins and rejected tokens", ["reason"], registry=REGISTRY)
RATE_LIMIT_REJECTIONS = Counter(
    "kumbh_rate_limit_rejections_total", "Requests rejected by rate limiting", ["policy"], registry=REGISTRY
)
RATE_LIMITER_ERRORS = Counter(
    "kumbh_rate_limiter_errors_total", "Shared limiter unavailable", ["policy", "outcome"], registry=REGISTRY
)
CASES_CREATED = Counter("kumbh_cases_created_total", "Cases registered", ["case_type"], registry=REGISTRY)
STATUS_TRANSITIONS = Counter(
    "kumbh_status_transitions_total", "Case status transitions", ["to_status"], registry=REGISTRY
)
MATCH_SUGGESTIONS = Counter(
    "kumbh_match_suggestions_total", "Automatic match suggestions stored", ["domain", "band"], registry=REGISTRY
)
MATCHING_FAILURES = Counter("kumbh_matching_failures_total", "Automatic matching runs that failed", registry=REGISTRY)
FACE_ANALYSES = Counter(
    "kumbh_face_analyses_total", "Face analysis outcomes for person photographs", ["status"], registry=REGISTRY
)
FACE_CANDIDATES = Counter("kumbh_face_candidates_total", "Face-based match candidates stored", registry=REGISTRY)
FACE_FAILURES = Counter("kumbh_face_failures_total", "Face pipeline runs that failed", registry=REGISTRY)
SCAN_OUTCOMES = Counter("kumbh_scan_outcomes_total", "Malware scan outcomes", ["outcome"], registry=REGISTRY)
NOTIFICATION_OUTCOMES = Counter(
    "kumbh_notification_outcomes_total", "Notification delivery outcomes", ["channel", "outcome"], registry=REGISTRY
)
RETENTION_ACTIONS = Counter(
    "kumbh_retention_actions_total", "Retention job outcomes", ["policy", "outcome"], registry=REGISTRY
)


class DatabaseStateCollector(Collector):
    """Pool usage and work-queue depth, read at scrape time so every replica reports the shared truth."""

    QUEUE_QUERIES: dict[str, str] = {
        "attachments_quarantined": "SELECT count(*) FROM case_attachments WHERE scan_status IN ('QUARANTINED', 'SCANNING')",
        "attachments_scan_failed": "SELECT count(*) FROM case_attachments WHERE scan_status = 'SCAN_FAILED'",
        "matches_awaiting_review": "SELECT count(*) FROM case_matches WHERE status = 'PROPOSED'",
        "face_matches_awaiting_review": (
            "SELECT count(*) FROM case_matches WHERE status = 'PROPOSED' AND source = 'FACE'"
        ),
        "staff_notifications_unread": "SELECT count(*) FROM staff_notifications WHERE status = 'UNREAD'",
        "notifications_pending": "SELECT count(*) FROM notifications WHERE status IN ('PENDING', 'SENDING')",
        "notifications_failed": "SELECT count(*) FROM notifications WHERE status = 'FAILED'",
        "worker_heartbeats_not_ok": (
            "SELECT count(*) FROM worker_heartbeats "
            "WHERE status <> 'ok' OR heartbeat_at < now() - interval '3 minutes'"
        ),
    }

    def collect(self):
        from sqlalchemy import text

        from app.database import engine

        pool = engine.pool
        usage = GaugeMetricFamily("kumbh_db_pool_connections", "Database pool connections", labels=["state"])
        for state, fn in (("checked_out", "checkedout"), ("idle", "checkedin"), ("overflow", "overflow")):
            if hasattr(pool, fn):
                usage.add_metric([state], float(getattr(pool, fn)()))
        yield usage

        queues = GaugeMetricFamily("kumbh_queue_depth", "Rows waiting in database-backed work queues", labels=["queue"])
        up = GaugeMetricFamily("kumbh_database_up", "1 if the metrics query reached the database")
        try:
            with engine.connect() as conn:
                for name, sql in self.QUEUE_QUERIES.items():
                    try:
                        with conn.begin_nested():
                            queues.add_metric([name], float(conn.execute(text(sql)).scalar() or 0))
                    except Exception:  # noqa: BLE001 - a missing table must not break the scrape
                        logger.debug("queue metric unavailable", extra={"queue": name})
            up.add_metric([], 1.0)
        except Exception:  # noqa: BLE001
            up.add_metric([], 0.0)
        yield queues
        yield up


REGISTRY.register(DatabaseStateCollector())


def count_after_commit(db, counter, *labels: str) -> None:
    """Increments the counter only if the session's current transaction commits."""
    db.info.setdefault("pending_metrics", []).append((counter, labels))


@event.listens_for(Session, "after_commit")
def _flush_pending_metrics(session: Session) -> None:
    for counter, labels in session.info.pop("pending_metrics", []):
        counter.labels(*labels).inc()


@event.listens_for(Session, "after_rollback")
def _drop_pending_metrics(session: Session) -> None:
    session.info.pop("pending_metrics", None)
