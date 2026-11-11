"""Offline synchronization: push client-created records, pull incremental updates, report status."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.audit import AuditAction, record_audit
from app.auth.permissions import Permission, require_permission
from app.auth.scope import case_visibility_clause
from app.common.errors import AppError, NotFound, ValidationFailed
from app.common.time import utcnow
from app.config import get_settings
from app.models import Case, SyncDevice, User
from app.schemas.sync import (
    SyncOperation,
    SyncPullItem,
    SyncPullResponse,
    SyncPushRecord,
    SyncPushRequest,
    SyncPushResponse,
    SyncRecordOutcome,
    SyncRecordResult,
    SyncStatusResponse,
)
from app.schemas.workflow import NoteCreate
from app.services import cases as case_service
from app.services import workflow as workflow_service
from app.services.access import load_case
from app.services.case_types import CASE_TYPE_SPECS, CaseTypeSpec, spec_for


def _encode_cursor(sync_changed_at: datetime, case_id: uuid.UUID) -> str:
    payload = {"t": sync_changed_at.isoformat(), "id": str(case_id)}
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        return datetime.fromisoformat(raw["t"]), uuid.UUID(raw["id"])
    except Exception as exc:  # noqa: BLE001 - any malformed cursor is a client error
        raise ValidationFailed("Invalid sync cursor", code="invalid_cursor") from exc


def _upsert_device(
    db: Session,
    user: User,
    *,
    device_id: str,
    client_id: str | None = None,
    pending_count: int | None = None,
    failed_count: int | None = None,
) -> SyncDevice:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "user_id": user.id,
        "device_id": device_id,
        "client_id": client_id,
        "pending_count": pending_count,
        "failed_count": failed_count,
    }
    update: dict[str, Any] = {"updated_at": func.now()}
    if client_id is not None:
        update["client_id"] = client_id
    if pending_count is not None:
        update["pending_count"] = pending_count
    if failed_count is not None:
        update["failed_count"] = failed_count
    stmt = (
        insert(SyncDevice)
        .values(**values)
        .on_conflict_do_update(
            constraint="uq_sync_devices_user_device",
            set_=update,
        )
        .returning(SyncDevice.id)
    )
    device_row_id = db.execute(stmt).scalar_one()
    db.flush()
    return db.get(SyncDevice, device_row_id)


def current_server_cursor(db: Session, user: User) -> str | None:
    lag = timedelta(seconds=get_settings().sync_pull_lag_seconds)
    cutoff = utcnow() - lag
    row = db.execute(
        select(Case.sync_changed_at, Case.id)
        .where(case_visibility_clause(user), Case.sync_changed_at <= cutoff, Case.archived_at.is_(None))
        .order_by(Case.sync_changed_at.desc(), Case.id.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    return _encode_cursor(row[0], row[1])


def _error_result(record: SyncPushRecord, exc: Exception) -> SyncRecordResult:
    if isinstance(exc, AppError):
        return SyncRecordResult(
            client_record_id=record.client_record_id,
            operation=record.operation,
            outcome=SyncRecordOutcome.FAILED,
            error_code=exc.code,
            error_message=exc.message,
            error_details=exc.details,
        )
    if isinstance(exc, ValidationError):
        return SyncRecordResult(
            client_record_id=record.client_record_id,
            operation=record.operation,
            outcome=SyncRecordOutcome.FAILED,
            error_code="validation_failed",
            error_message="Record validation failed",
            error_details=exc.errors(),
        )
    return SyncRecordResult(
        client_record_id=record.client_record_id,
        operation=record.operation,
        outcome=SyncRecordOutcome.FAILED,
        error_code="internal_error",
        error_message="Unexpected error while synchronizing this record",
    )


def _resolve_case_id(
    db: Session, user: User, record: SyncPushRecord, *, device_id: str, created_map: dict[str, uuid.UUID]
) -> uuid.UUID:
    if record.case_id is not None:
        return record.case_id
    assert record.case_client_record_id is not None
    if record.case_client_record_id in created_map:
        return created_map[record.case_client_record_id]
    existing = case_service.find_case_by_client_record(
        db, user, device_id=device_id, client_record_id=record.case_client_record_id
    )
    if existing is None:
        raise NotFound(
            "Referenced offline case has not been synchronized yet",
            code="case_client_record_not_found",
            details={"case_client_record_id": record.case_client_record_id},
        )
    return existing.id


def _create_case_record(
    db: Session, user: User, record: SyncPushRecord, *, device_id: str, client_id: str | None
) -> SyncRecordResult:
    spec: CaseTypeSpec = CASE_TYPE_SPECS[record.case_type]
    payload_data = {
        **record.data,
        "client_id": client_id,
        "device_id": device_id,
        "client_record_id": record.client_record_id,
        "client_created_at": record.client_created_at,
    }
    payload = spec.create_schema.model_validate(payload_data)
    case, replayed = case_service.create_case(db, user, spec, payload)
    return SyncRecordResult(
        client_record_id=record.client_record_id,
        operation=record.operation,
        outcome=SyncRecordOutcome.DUPLICATE if replayed else SyncRecordOutcome.SYNCED,
        case_id=case.id,
        case_number=case.case_number,
        version=case.version,
        server_updated_at=case.sync_changed_at or case.updated_at,
    )


def _update_idempotency_key(device_id: str, client_record_id: str) -> str:
    key = f"sync-upd-{device_id}-{client_record_id}"
    if len(key) < 8:
        key = key.ljust(8, "0")
    return key[:128]


def _update_case_record(
    db: Session,
    user: User,
    record: SyncPushRecord,
    *,
    device_id: str,
    created_map: dict[str, uuid.UUID],
) -> SyncRecordResult:
    from app.models import IdempotencyKey

    case_id = _resolve_case_id(db, user, record, device_id=device_id, created_map=created_map)
    case = load_case(db, user, case_id)
    spec = spec_for(case.case_type)
    payload_data = {**record.data, "expected_version": record.base_version}
    payload = spec.update_schema.model_validate(payload_data)
    idem_key = _update_idempotency_key(device_id, record.client_record_id)

    # A prior successful push of this client_record_id is a durable retry, even if the case
    # version has moved on since then. Check before the optimistic-lock comparison.
    existing_key = db.scalar(
        select(IdempotencyKey).where(IdempotencyKey.user_id == user.id, IdempotencyKey.key == idem_key)
    )
    if existing_key is not None and existing_key.resource_id is not None and existing_key.expires_at > utcnow():
        refreshed = load_case(db, user, case_id, spec=spec)
        return SyncRecordResult(
            client_record_id=record.client_record_id,
            operation=record.operation,
            outcome=SyncRecordOutcome.DUPLICATE,
            case_id=refreshed.id,
            case_number=refreshed.case_number,
            version=refreshed.version,
            server_updated_at=refreshed.sync_changed_at or refreshed.updated_at,
        )

    if record.base_version != case.version:
        read = case_service.to_read(db, case, spec, actor=user)
        return SyncRecordResult(
            client_record_id=record.client_record_id,
            operation=record.operation,
            outcome=SyncRecordOutcome.CONFLICT,
            case_id=case.id,
            case_number=case.case_number,
            version=case.version,
            server_updated_at=case.sync_changed_at or case.updated_at,
            error_code="case_changed",
            error_message="The case changed on the server; reload and resolve before retrying",
            error_details={
                "case_id": str(case.id),
                "expected_version": record.base_version,
                "current_version": case.version,
            },
            case=read.model_dump(mode="json"),
        )
    updated, replayed = case_service.update_case(
        db, user, spec, case_id, payload, idempotency_key=idem_key
    )
    return SyncRecordResult(
        client_record_id=record.client_record_id,
        operation=record.operation,
        outcome=SyncRecordOutcome.DUPLICATE if replayed else SyncRecordOutcome.SYNCED,
        case_id=updated.id,
        case_number=updated.case_number,
        version=updated.version,
        server_updated_at=updated.sync_changed_at or updated.updated_at,
    )


def _add_note_record(
    db: Session,
    user: User,
    record: SyncPushRecord,
    *,
    device_id: str,
    client_id: str | None,
    created_map: dict[str, uuid.UUID],
) -> SyncRecordResult:
    case_id = _resolve_case_id(db, user, record, device_id=device_id, created_map=created_map)
    payload_data = {
        **record.data,
        "client_id": client_id,
        "device_id": device_id,
        "client_record_id": record.client_record_id,
        "client_created_at": record.client_created_at,
    }
    note_req = NoteCreate.model_validate(payload_data)
    note, replayed = workflow_service.add_note(db, user, case_id, note_req)
    case = load_case(db, user, case_id)
    return SyncRecordResult(
        client_record_id=record.client_record_id,
        operation=record.operation,
        outcome=SyncRecordOutcome.DUPLICATE if replayed else SyncRecordOutcome.SYNCED,
        case_id=case.id,
        case_number=case.case_number,
        note_id=note.id,
        version=case.version,
        server_updated_at=case.sync_changed_at or case.updated_at,
    )


def push(
    db: Session,
    user: User,
    request: SyncPushRequest,
    *,
    idempotency_key: str | None = None,  # reserved; per-record identity is the durable guard
) -> SyncPushResponse:
    del idempotency_key  # Accepted at the API for compatibility; not required for correctness.
    require_permission(user, Permission.CASE_CREATE)
    settings = get_settings()
    if len(request.records) > settings.sync_max_batch:
        raise ValidationFailed(
            f"A sync push may contain at most {settings.sync_max_batch} records",
            code="sync_batch_too_large",
            details={"max": settings.sync_max_batch, "received": len(request.records)},
        )

    device = _upsert_device(
        db,
        user,
        device_id=request.device_id,
        client_id=request.client_id,
        pending_count=request.pending_count,
        failed_count=request.failed_count,
    )
    device.last_push_at = utcnow()
    db.commit()

    results: list[SyncRecordResult] = []
    created_map: dict[str, uuid.UUID] = {}
    synced = duplicates = conflicts = failed = 0

    for record in request.records:
        try:
            if record.operation is SyncOperation.CREATE_CASE:
                result = _create_case_record(
                    db, user, record, device_id=request.device_id, client_id=request.client_id
                )
                if result.case_id is not None:
                    created_map[record.client_record_id] = result.case_id
            elif record.operation is SyncOperation.UPDATE_CASE:
                result = _update_case_record(
                    db, user, record, device_id=request.device_id, created_map=created_map
                )
                if result.outcome is SyncRecordOutcome.CONFLICT:
                    record_audit(
                        db,
                        action=AuditAction.SYNC_CONFLICT,
                        entity_type="case",
                        entity_id=result.case_id,
                        actor_user_id=user.id,
                        after={
                            "client_record_id": record.client_record_id,
                            "device_id": request.device_id,
                            "expected_version": record.base_version,
                            "current_version": result.version,
                        },
                    )
                    db.commit()
            else:
                result = _add_note_record(
                    db,
                    user,
                    record,
                    device_id=request.device_id,
                    client_id=request.client_id,
                    created_map=created_map,
                )
        except (AppError, ValidationError) as exc:
            db.rollback()
            result = _error_result(record, exc)
        except Exception:  # noqa: BLE001 - isolate one bad record; do not abort the batch
            db.rollback()
            result = _error_result(record, Exception("internal"))

        results.append(result)
        if result.outcome is SyncRecordOutcome.SYNCED:
            synced += 1
        elif result.outcome is SyncRecordOutcome.DUPLICATE:
            duplicates += 1
        elif result.outcome is SyncRecordOutcome.CONFLICT:
            conflicts += 1
        else:
            failed += 1

    device = db.scalar(
        select(SyncDevice).where(SyncDevice.user_id == user.id, SyncDevice.device_id == request.device_id)
    )
    if device is not None:
        device.last_push_at = utcnow()
        if synced or duplicates:
            device.last_successful_push_at = utcnow()
        if request.pending_count is not None:
            device.pending_count = request.pending_count
        if request.failed_count is not None:
            device.failed_count = request.failed_count
        device.updated_at = utcnow()

    record_audit(
        db,
        action=AuditAction.SYNC_PUSH,
        entity_type="sync_device",
        entity_id=device.id if device else None,
        actor_user_id=user.id,
        after={
            "device_id": request.device_id,
            "client_id": request.client_id,
            "record_count": len(request.records),
            "synced": synced,
            "duplicates": duplicates,
            "conflicts": conflicts,
            "failed": failed,
            "outcomes": [
                {"client_record_id": r.client_record_id, "operation": r.operation, "outcome": r.outcome}
                for r in results
            ],
        },
    )
    db.commit()

    return SyncPushResponse(
        device_id=request.device_id,
        results=results,
        synced=synced,
        duplicates=duplicates,
        conflicts=conflicts,
        failed=failed,
        server_time=utcnow(),
    )


def pull(
    db: Session,
    user: User,
    *,
    device_id: str,
    cursor: str | None,
    limit: int,
    client_id: str | None = None,
) -> SyncPullResponse:
    require_permission(user, Permission.CASE_READ)
    settings = get_settings()
    limit = max(1, min(limit, settings.sync_max_batch))
    lag = timedelta(seconds=settings.sync_pull_lag_seconds)
    cutoff = utcnow() - lag

    conditions = [
        case_visibility_clause(user),
        Case.archived_at.is_(None),
        Case.sync_changed_at <= cutoff,
    ]
    if cursor:
        cursor_at, cursor_id = _decode_cursor(cursor)
        conditions.append(
            or_(
                Case.sync_changed_at > cursor_at,
                and_(Case.sync_changed_at == cursor_at, Case.id > cursor_id),
            )
        )

    rows = list(
        db.scalars(
            select(Case)
            .where(*conditions)
            .order_by(Case.sync_changed_at.asc(), Case.id.asc())
            .limit(limit + 1)
        ).unique()
    )
    has_more = len(rows) > limit
    rows = rows[:limit]

    # Eager-load subtype details for to_read.
    items: list[SyncPullItem] = []
    for case in rows:
        # Reload with details via repository path used elsewhere.
        full = load_case(db, user, case.id)
        read = case_service.to_read(db, full, actor=user)
        items.append(
            SyncPullItem(
                case=read.model_dump(mode="json"),
                server_updated_at=full.sync_changed_at,
                version=full.version,
            )
        )

    next_cursor = None
    if items:
        last = rows[-1]
        next_cursor = _encode_cursor(last.sync_changed_at, last.id)

    device = _upsert_device(db, user, device_id=device_id, client_id=client_id)
    device.last_pull_at = utcnow()
    device.last_successful_pull_at = utcnow()
    if next_cursor is not None:
        device.last_pull_cursor = next_cursor
    device.updated_at = utcnow()
    record_audit(
        db,
        action=AuditAction.SYNC_PULL,
        entity_type="sync_device",
        entity_id=device.id,
        actor_user_id=user.id,
        after={
            "device_id": device_id,
            "cursor": cursor,
            "returned": len(items),
            "has_more": has_more,
            "next_cursor": next_cursor,
        },
    )
    db.commit()

    return SyncPullResponse(
        items=items,
        next_cursor=next_cursor,
        has_more=has_more,
        server_time=utcnow(),
    )


def status(
    db: Session,
    user: User,
    *,
    device_id: str,
    pending_count: int | None = None,
    failed_count: int | None = None,
    client_id: str | None = None,
) -> SyncStatusResponse:
    require_permission(user, Permission.CASE_READ)
    device = _upsert_device(
        db,
        user,
        device_id=device_id,
        client_id=client_id,
        pending_count=pending_count,
        failed_count=failed_count,
    )
    db.commit()
    device = db.scalar(
        select(SyncDevice).where(SyncDevice.user_id == user.id, SyncDevice.device_id == device_id)
    )
    last_push = device.last_successful_push_at if device else None
    last_pull = device.last_successful_pull_at if device else None
    last_sync = None
    if last_push and last_pull:
        last_sync = max(last_push, last_pull)
    else:
        last_sync = last_push or last_pull
    return SyncStatusResponse(
        device_id=device_id,
        last_successful_sync_at=last_sync,
        last_successful_push_at=last_push,
        last_successful_pull_at=last_pull,
        pending_count=device.pending_count if device else pending_count,
        failed_count=device.failed_count if device else failed_count,
        server_cursor=current_server_cursor(db, user),
        server_time=utcnow(),
    )
