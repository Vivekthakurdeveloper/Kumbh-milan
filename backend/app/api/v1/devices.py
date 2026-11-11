"""Push token registration for authenticated staff or verified citizen status sessions."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.api.v1.responses import errors
from app.auth.dependencies import DbSession
from app.auth.tokens import decode_access_token
from app.common.errors import Unauthenticated
from app.common.time import utcnow
from app.database import get_db
from app.models import RefreshToken, User
from app.models.enums import Role
from app.schemas.public import PushTokenDelete, PushTokenRegister
from app.services import devices as device_service

router = APIRouter(prefix="/devices", tags=["Devices"])
_bearer = HTTPBearer(auto_error=False)


def _optional_staff(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Annotated[Session, Depends(get_db)],
) -> User | None:
    if credentials is None or credentials.scheme.lower() != "bearer":
        return None
    try:
        claims = decode_access_token(credentials.credentials)
        user_id = uuid.UUID(claims["sub"])
        session_id = uuid.UUID(claims["sid"])
    except Exception:  # noqa: BLE001
        return None
    user = db.get(User, user_id)
    session = db.get(RefreshToken, session_id)
    if (
        user is None
        or not user.is_active
        or Role(user.role) is Role.PUBLIC
        or user.token_version != claims["tv"]
        or session is None
        or session.user_id != user.id
        or session.revoked_at is not None
        or session.expires_at <= utcnow()
    ):
        return None
    return user


@router.post(
    "/push-token",
    response_class=Response,
    status_code=status.HTTP_204_NO_CONTENT,
    responses=errors(401, 404, 422),
)
def register_push_token(
    payload: PushTokenRegister,
    db: DbSession,
    staff: Annotated[User | None, Depends(_optional_staff)] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    if staff is None and not authorization:
        raise Unauthenticated("Authentication required")
    device_service.register_token(
        db,
        payload=payload,
        staff_user=staff,
        citizen_authorization=None if staff else authorization,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/push-token",
    response_class=Response,
    status_code=status.HTTP_204_NO_CONTENT,
    responses=errors(401, 404, 422),
)
def delete_push_token(
    payload: PushTokenDelete,
    db: DbSession,
    staff: Annotated[User | None, Depends(_optional_staff)] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    if staff is None and not authorization:
        raise Unauthenticated("Authentication required")
    device_service.delete_token(
        db,
        token=payload.token,
        staff_user=staff,
        citizen_authorization=None if staff else authorization,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
