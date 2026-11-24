"""Re-encrypts stored ciphertext under the current keys so retired keys can be removed from configuration.

Safe to interrupt and re-run: rows already on the current key are skipped, and each row is swapped only if it still
holds the ciphertext that was read, so a concurrent write is never overwritten.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import inspect, select, text, update
from sqlalchemy.orm import Session

from app.audit import AuditAction, record_audit
from app.common import crypto
from app.faces import crypto as face_crypto
from app.models import (
    CaseContact,
    CaseHandover,
    FaceEmbedding,
    Grievance,
    GrievanceEvent,
    IntegrationDestination,
    Notification,
    PublicSighting,
    PushDeviceToken,
    User,
)

CONTACT_COLUMNS: tuple[tuple[type, str], ...] = (
    (CaseContact, "contact_ciphertext"),
    (CaseContact, "email_ciphertext"),
    (CaseHandover, "claimant_ciphertext"),
    (CaseHandover, "witness_ciphertext"),
    (CaseHandover, "verification_ciphertext"),
    (CaseHandover, "acknowledgement_ciphertext"),
    (CaseHandover, "rejection_ciphertext"),
    (Grievance, "description_ciphertext"),
    (Grievance, "mobile_ciphertext"),
    (Grievance, "resolution_ciphertext"),
    (GrievanceEvent, "body_ciphertext"),
    (IntegrationDestination, "secret_ciphertext"),
    (Notification, "recipient_ciphertext"),
    (PublicSighting, "reporter_mobile_ciphertext"),
    (PushDeviceToken, "token_ciphertext"),
    (User, "totp_secret_ciphertext"),
)
FACE_COLUMNS: tuple[tuple[type, str], ...] = ((FaceEmbedding, "embedding_ciphertext"),)


@dataclass
class ColumnReport:
    scanned: int = 0
    current: int = 0
    rotated: int = 0
    undecryptable: list[str] = field(default_factory=list)


def _targets() -> list[tuple[type, str, Fernet, MultiFernet]]:
    targets = [(m, c, crypto.contact_primary(), crypto.contact_keyring()) for m, c in CONTACT_COLUMNS]
    targets += [(m, c, face_crypto.face_primary(), face_crypto.face_keyring()) for m, c in FACE_COLUMNS]
    return targets


def _on_primary(primary: Fernet, token: str) -> bool:
    try:
        primary.decrypt(token.encode("ascii"))
    except InvalidToken:
        return False
    return True


def rotate(db: Session, *, apply: bool, batch_size: int = 500) -> dict[str, ColumnReport]:
    """Counts (dry run) or re-encrypts ciphertext not under the current key. Ids of unreadable rows are reported."""
    reports: dict[str, ColumnReport] = {}
    for model, column, primary, keyring in _targets():
        report = reports.setdefault(f"{model.__tablename__}.{column}", ColumnReport())
        attr = getattr(model, column)
        (pk,) = inspect(model).primary_key
        after: uuid.UUID | None = None
        while True:
            query = select(pk, attr).where(attr.is_not(None)).order_by(pk).limit(batch_size)
            if after is not None:
                query = query.where(pk > after)
            rows = db.execute(query).all()
            if not rows:
                break
            after = rows[-1][0]
            if apply:
                db.execute(text("SELECT set_config('kumbh.key_rotation', 'on', true)"))
            for row_id, token in rows:
                report.scanned += 1
                if _on_primary(primary, token):
                    report.current += 1
                    continue
                try:
                    rotated = keyring.rotate(token.encode("ascii")).decode("ascii")
                except InvalidToken:
                    report.undecryptable.append(str(row_id))
                    continue
                if apply:
                    swapped = db.execute(
                        update(model).where(pk == row_id, attr == token).values({column: rotated})
                        .execution_options(synchronize_session=False)
                    ).rowcount
                    report.rotated += swapped
                else:
                    report.rotated += 1
            if apply:
                db.commit()
            else:
                db.rollback()
    if apply:
        record_audit(
            db, action=AuditAction.ENCRYPTION_KEYS_ROTATED, entity_type="encryption_keys", actor_user_id=None,
            after={name: {"rotated": r.rotated, "undecryptable": len(r.undecryptable)}
                   for name, r in reports.items() if r.rotated or r.undecryptable},
        )
        db.commit()
    return reports
