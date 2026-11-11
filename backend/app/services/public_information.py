"""Approved bilingual public help-desk and announcement content."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import AuditAction, record_audit
from app.auth.permissions import Permission, require_permission
from app.common.errors import Conflict, NotFound
from app.common.time import utcnow
from app.models import PublicInformationItem, User
from app.schemas.public import PublicInformationCreate, PublicInformationUpdate


def list_published(db: Session) -> list[PublicInformationItem]:
    return list(
        db.scalars(
            select(PublicInformationItem)
            .where(PublicInformationItem.is_published.is_(True))
            .order_by(PublicInformationItem.sort_order, PublicInformationItem.created_at)
        )
    )


def list_all(db: Session, actor: User) -> list[PublicInformationItem]:
    require_permission(actor, Permission.PUBLIC_INFORMATION_MANAGE)
    return list(
        db.scalars(
            select(PublicInformationItem).order_by(
                PublicInformationItem.sort_order, PublicInformationItem.created_at.desc()
            )
        )
    )


def create_item(db: Session, actor: User, payload: PublicInformationCreate) -> PublicInformationItem:
    require_permission(actor, Permission.PUBLIC_INFORMATION_MANAGE)
    item = PublicInformationItem(
        id=uuid.uuid4(),
        kind=payload.kind,
        title_en=payload.title_en,
        title_hi=payload.title_hi,
        body_en=payload.body_en,
        body_hi=payload.body_hi,
        contact_phone=payload.contact_phone,
        contact_email=str(payload.contact_email) if payload.contact_email else None,
        location_text=payload.location_text,
        sort_order=payload.sort_order,
        is_published=payload.publish,
        published_at=utcnow() if payload.publish else None,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )
    db.add(item)
    record_audit(
        db,
        action=AuditAction.PUBLIC_INFORMATION_UPDATED,
        entity_type="public_information_item",
        entity_id=item.id,
        actor_user_id=actor.id,
        after={"kind": payload.kind, "published": payload.publish},
    )
    db.commit()
    db.refresh(item)
    return item


def update_item(
    db: Session, actor: User, item_id: uuid.UUID, payload: PublicInformationUpdate
) -> PublicInformationItem:
    require_permission(actor, Permission.PUBLIC_INFORMATION_MANAGE)
    item = db.get(PublicInformationItem, item_id)
    if item is None:
        raise NotFound("Public information item not found", code="public_information_not_found")
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        if key == "contact_email" and value is not None:
            value = str(value)
        setattr(item, key, value)
    item.updated_by_user_id = actor.id
    record_audit(
        db,
        action=AuditAction.PUBLIC_INFORMATION_UPDATED,
        entity_type="public_information_item",
        entity_id=item.id,
        actor_user_id=actor.id,
        after={"fields": sorted(data)},
    )
    db.commit()
    db.refresh(item)
    return item


def publish_item(db: Session, actor: User, item_id: uuid.UUID, *, publish: bool) -> PublicInformationItem:
    require_permission(actor, Permission.PUBLIC_INFORMATION_MANAGE)
    item = db.get(PublicInformationItem, item_id)
    if item is None:
        raise NotFound("Public information item not found", code="public_information_not_found")
    if publish and item.is_published:
        raise Conflict("Item is already published", code="already_published")
    if not publish and not item.is_published:
        raise Conflict("Item is already unpublished", code="already_unpublished")
    item.is_published = publish
    item.updated_by_user_id = actor.id
    if publish:
        item.published_at = utcnow()
        item.unpublished_at = None
    else:
        item.unpublished_at = utcnow()
    record_audit(
        db,
        action=AuditAction.PUBLIC_INFORMATION_UPDATED,
        entity_type="public_information_item",
        entity_id=item.id,
        actor_user_id=actor.id,
        after={"published": publish},
    )
    db.commit()
    db.refresh(item)
    return item
