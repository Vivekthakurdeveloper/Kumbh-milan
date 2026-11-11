"""Operational commands: python -m app.cli <command> [options]."""

import argparse
import getpass
import os
import sys
import uuid
from datetime import timedelta

from sqlalchemy import delete, select

from app.audit import AuditAction, record_audit
from app.auth.passwords import hash_password, validate_new_password
from app.common.errors import AppError
from app.common.time import utcnow
from app.database import SessionLocal
from app.models import IdempotencyKey, LoginAttempt, PasswordResetToken, RefreshToken, User
from app.models.enums import Role
from app.storage import get_storage


def create_admin(args: argparse.Namespace) -> int:
    password = os.environ.get("KUMBH_ADMIN_PASSWORD") or getpass.getpass("Password for the new admin: ")
    username = args.username.strip().lower()
    try:
        validate_new_password(password, username=username)
    except AppError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return 2
    with SessionLocal() as db:
        if db.scalar(select(User.id).where(User.username == username)):
            print(f"error: user {username!r} already exists", file=sys.stderr)
            return 1
        user = User(
            id=uuid.uuid4(), full_name=args.full_name, username=username, email=args.email,
            password_hash=hash_password(password), role=Role.ADMIN, station_id=None,
        )
        db.add(user)
        record_audit(
            db, action=AuditAction.USER_CREATED, entity_type="user", entity_id=user.id, actor_user_id=None,
            after={"username": username, "role": Role.ADMIN, "source": "cli"},
        )
        db.commit()
    print(f"Created admin {username} ({user.id})")
    return 0


def ensure_bucket(_: argparse.Namespace) -> int:
    get_storage().ensure_bucket()
    print(f"Bucket {get_storage().bucket!r} is ready and private")
    return 0


def purge_expired(args: argparse.Namespace) -> int:
    """Removes expired operational rows. Case data, notes, and audit events are never touched."""
    now = utcnow()
    with SessionLocal() as db:
        keys = db.execute(delete(IdempotencyKey).where(IdempotencyKey.expires_at < now)).rowcount
        attempts = db.execute(
            delete(LoginAttempt).where(LoginAttempt.created_at < now - timedelta(days=args.login_attempt_days))
        ).rowcount
        sessions = db.execute(
            delete(RefreshToken).where(RefreshToken.expires_at < now - timedelta(days=args.session_days))
        ).rowcount
        from app.models import OtpChallenge

        otps = db.execute(
            delete(OtpChallenge).where(OtpChallenge.expires_at < now - timedelta(days=1))
        ).rowcount
        resets = db.execute(
            delete(PasswordResetToken).where(PasswordResetToken.expires_at < now - timedelta(days=1))
        ).rowcount
        db.commit()
    print(
        f"Purged {keys} idempotency keys, {attempts} login attempts, {sessions} expired sessions, "
        f"{otps} OTP challenges, {resets} password reset tokens"
    )
    return 0


def scan_worker(args: argparse.Namespace) -> int:
    """Scans quarantined uploads. --once processes one batch and exits (for cron/one-off tasks)."""
    import logging
    import signal
    import time

    from app.config import get_settings
    from app.observability import configure_logging
    from app.services import attachments as attachment_service
    from app.storage.scanner import get_scanner

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    log = logging.getLogger("kumbh_setu.scan_worker")
    scanner = get_scanner()
    if scanner.mode == "disabled":
        log.warning("SCAN_MODE=disabled: uploads are marked SKIPPED_DEV (development only)")
    storage = get_storage()
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    from app.workers.runtime import beat

    while not stopping:
        beat("scan")
        processed = 0
        with SessionLocal() as db:
            released = attachment_service.release_stale_scans(db)
            if released:
                log.warning("Released stale scans", extra={"count": released})
            for attachment_id in attachment_service.due_scans(db, args.batch_size):
                outcome = attachment_service.process_scan(db, storage, scanner, attachment_id)
                log.info("Scan processed", extra={"attachment_id": str(attachment_id), "outcome": outcome})
                processed += 1
        if args.once:
            break
        if not processed:
            time.sleep(args.interval)
    return 0


def link_locations(args: argparse.Namespace) -> int:
    """Dry run unless --apply. Links by exact (normalised) place name only; coordinates are never set."""
    from collections import Counter

    from app.services import geo

    with SessionLocal() as db:
        proposals = geo.plan_location_links(db, limit=args.limit)
        counts = Counter(p.outcome for p in proposals)
        for p in proposals:
            if p.outcome != "no_match" or args.verbose:
                print(f"{p.outcome:9} {p.case_number}  location_id={p.location_id or '-'}")
        print(f"link={counts['link']} ambiguous={counts['ambiguous']} no_match={counts['no_match']}")
        if not args.apply:
            print("Dry run: nothing changed. Re-run with --apply to link the 'link' rows.")
            return 0
        applied = geo.apply_location_links(db, proposals)
    print(f"Linked {applied} cases")
    return 0


def rematch(args: argparse.Namespace) -> int:
    """Re-runs automatic matching for recent active missing-person and lost-item cases."""
    from app.services import matching

    with SessionLocal() as db:
        scanned, created = matching.rematch(db, since_days=args.since_days, limit=args.limit)
    print(f"Scored {scanned} cases; stored {created} new suggestions")
    return 0


def retention_execute(args: argparse.Namespace) -> int:
    """Executes an APPROVED retention run with RETENTION_DATABASE_URL (owner) if set. Approval happens in the API."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.config import get_settings
    from app.services import retention

    settings = get_settings()
    url = settings.retention_database_url or settings.database_url
    engine = create_engine(url, pool_size=1, max_overflow=0)
    try:
        with sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)() as db:
            run = retention.execute_run(db, get_storage(), uuid.UUID(args.run_id), executor=None)
            print(f"Run {run.id}: {run.status} {run.execution_report}")
            return 0 if run.status == "EXECUTED" else 1
    except AppError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return 2
    finally:
        engine.dispose()


def notifications_worker(args: argparse.Namespace) -> int:
    """Delivers queued notifications through the configured provider (retries with backoff)."""
    import logging
    import signal
    import time

    from app.config import get_settings
    from app.notifications import get_provider
    from app.observability import configure_logging
    from app.services import notifications

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    log = logging.getLogger("kumbh_setu.notifications_worker")
    provider = get_provider()
    if not provider.enabled:
        log.warning("NOTIFICATION_PROVIDER=disabled: queued messages stay PENDING")
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    from app.workers.runtime import beat

    while not stopping:
        beat("notifications")
        with SessionLocal() as db:
            notifications.release_stale(db)
            outcomes = notifications.deliver_due(db, provider, limit=args.batch_size)
        if any(outcomes.values()):
            log.info("Notifications processed", extra=outcomes)
        if args.once:
            break
        if not any(outcomes.values()):
            time.sleep(args.interval)
    return 0


def maintenance_worker(args: argparse.Namespace) -> int:
    """Cleanup and retention liveness. It does not execute retention anonymisation."""
    import logging
    import signal
    import time

    from app.observability import configure_logging
    from app.services import attachments as attachment_service
    from app.storage import get_storage
    from app.workers.runtime import beat

    from app.config import get_settings

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    log = logging.getLogger("kumbh_setu.maintenance_worker")
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    status = "ok"
    while not stopping:
        status = "ok"
        try:
            purge_expired(argparse.Namespace(login_attempt_days=args.login_attempt_days, session_days=args.session_days))
            with SessionLocal() as db:
                report = attachment_service.reconcile_objects(db, get_storage(), limit=args.limit)
                from app.services import faces as face_service

                purged_faces = face_service.purge_face_embeddings(db)
            log.info(
                "Object reconciliation",
                extra={**{k: report[k] for k in ("checked", "missing_objects")}, "face_embeddings_purged": purged_faces},
            )
        except Exception as exc:  # noqa: BLE001
            status = "degraded"
            log.warning("Maintenance pass failed", extra={"error": type(exc).__name__})
        beat("cleanup", status=status)
        beat("retention", status=status)
        if args.once:
            break
        time.sleep(args.interval)
    return 0 if status == "ok" else 1


def schema_status(_args: argparse.Namespace) -> int:
    """Print the applied Alembic revision without connection details."""
    from sqlalchemy import text

    from app.database import engine
    from app.database.revision import EXPECTED_SCHEMA_REVISION

    with engine.connect() as conn:
        versions = [row[0] for row in conn.execute(text("SELECT version_num FROM alembic_version"))]
    current = ",".join(versions) if versions else "missing"
    print(f"expected={EXPECTED_SCHEMA_REVISION} current={current}")
    return 0 if versions == [EXPECTED_SCHEMA_REVISION] else 1


def fetch_face_models(args: argparse.Namespace) -> int:
    from pathlib import Path

    from app.config import get_settings
    from app.faces.models_fetch import fetch_models

    dest = Path(get_settings().face_model_dir)
    paths = fetch_models(dest, force=args.force)
    for path in paths:
        print(path)
    return 0


def fetch_face_fixtures(args: argparse.Namespace) -> int:
    from pathlib import Path

    from app.faces.fixtures_fetch import fetch_fixtures_cli

    dest = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "faces"
    paths = fetch_fixtures_cli(dest, force=args.force)
    for path in paths:
        print(path)
    return 0


def face_backfill(args: argparse.Namespace) -> int:
    from app.services import faces as face_service
    from app.storage import get_storage

    with SessionLocal() as db:
        processed, candidates = face_service.backfill(db, get_storage(), limit=args.limit)
    print(f"Analysed {processed} photos; created {candidates} face candidates")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-admin", help="Create the first administrator (password from KUMBH_ADMIN_PASSWORD or prompt)")
    p.add_argument("--username", required=True)
    p.add_argument("--full-name", required=True)
    p.add_argument("--email")
    p.set_defaults(func=create_admin)

    p = sub.add_parser("ensure-bucket", help="Create the private media bucket if it does not exist")
    p.set_defaults(func=ensure_bucket)

    p = sub.add_parser("purge-expired", help="Delete expired idempotency keys, old login attempts, and old sessions")
    p.add_argument("--login-attempt-days", type=int, default=90)
    p.add_argument("--session-days", type=int, default=30)
    p.set_defaults(func=purge_expired)

    p = sub.add_parser("link-locations", help="Link historical cases to known locations by exact place name (dry run by default)")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--limit", type=int, default=10_000)
    p.add_argument("--verbose", action="store_true", help="Also list cases with no match")
    p.set_defaults(func=link_locations)

    p = sub.add_parser("rematch", help="Re-run automatic match suggestions for recent active cases")
    p.add_argument("--since-days", type=int, default=14)
    p.add_argument("--limit", type=int, default=5000)
    p.set_defaults(func=rematch)

    p = sub.add_parser("retention-execute", help="Execute an approved retention run (planned and approved via the API)")
    p.add_argument("run_id")
    p.set_defaults(func=retention_execute)

    p = sub.add_parser("notifications-worker", help="Deliver queued notifications")
    p.add_argument("--once", action="store_true")
    p.add_argument("--batch-size", type=int, default=50)
    p.add_argument("--interval", type=float, default=5.0)
    p.set_defaults(func=notifications_worker)

    p = sub.add_parser("scan-worker", help="Scan quarantined uploads with ClamAV (retries with backoff)")
    p.add_argument("--once", action="store_true")
    p.add_argument("--batch-size", type=int, default=20)
    p.add_argument("--interval", type=float, default=5.0)
    p.set_defaults(func=scan_worker)

    p = sub.add_parser("maintenance-worker", help="Purge expired sessions and report missing objects. Does not anonymise cases.")
    p.add_argument("--once", action="store_true")
    p.add_argument("--interval", type=float, default=60.0)
    p.add_argument("--limit", type=int, default=500)
    p.add_argument("--login-attempt-days", type=int, default=90)
    p.add_argument("--session-days", type=int, default=30)
    p.set_defaults(func=maintenance_worker)

    p = sub.add_parser("schema-status", help="Compare the database revision with the revision this build expects")
    p.set_defaults(func=schema_status)

    p = sub.add_parser("fetch-face-models", help="Download pinned YuNet and SFace ONNX models into FACE_MODEL_DIR")
    p.add_argument("--force", action="store_true", help="Re-download even if files already exist")
    p.set_defaults(func=fetch_face_models)

    p = sub.add_parser("fetch-face-fixtures", help="Download public-domain face photographs for integration tests")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=fetch_face_fixtures)

    p = sub.add_parser("face-backfill", help="Analyse person-case photos that have no face embedding yet")
    p.add_argument("--limit", type=int, default=500)
    p.set_defaults(func=face_backfill)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
