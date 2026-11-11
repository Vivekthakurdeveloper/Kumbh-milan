import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.permissions import Permission, require_permission
from app.auth.public_access import enforce_public_route_access
from app.auth.tokens import decode_access_token
from app.common.errors import Unauthenticated
from app.common.time import utcnow
from app.database import get_db
from app.models import RefreshToken, User
from app.models.enums import Role
from app.observability.metrics import AUTH_FAILURES

_bearer = HTTPBearer(auto_error=False, description="Access token from POST /api/v1/auth/login")


@dataclass(frozen=True)
class Principal:
    user: User
    session_id: uuid.UUID


def get_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Annotated[Session, Depends(get_db)],
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise Unauthenticated("Authentication required")
    claims = decode_access_token(credentials.credentials)
    try:
        user_id = uuid.UUID(claims["sub"])
        session_id = uuid.UUID(claims["sid"])
    except (ValueError, KeyError) as exc:
        AUTH_FAILURES.labels("invalid_token").inc()
        raise Unauthenticated("Invalid access token", code="invalid_token") from exc

    user = db.get(User, user_id)
    if user is None or not user.is_active or user.token_version != claims["tv"]:
        AUTH_FAILURES.labels("session_revoked").inc()
        raise Unauthenticated("Session is no longer valid", code="session_revoked")
    session = db.get(RefreshToken, session_id)
    if (
        session is None
        or session.user_id != user.id
        or session.revoked_at is not None
        or session.expires_at <= utcnow()
    ):
        AUTH_FAILURES.labels("session_revoked").inc()
        raise Unauthenticated("Session is no longer valid", code="session_revoked")
    if Role(user.role) is Role.PUBLIC:
        enforce_public_route_access(request.method, request.url.path)
    return Principal(user=user, session_id=session_id)


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
DbSession = Annotated[Session, Depends(get_db)]


def requires(permission: Permission) -> Callable[[Principal], None]:
    """Route dependency that rejects unauthorized roles before the request body is validated."""

    def check(principal: CurrentPrincipal) -> None:
        require_permission(principal.user, permission)

    return check
