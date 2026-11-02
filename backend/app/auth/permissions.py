from enum import StrEnum

from app.common.errors import Forbidden
from app.models import User
from app.models.enums import Role


class Permission(StrEnum):
    USER_MANAGE = "user:manage"
    STATION_MANAGE = "station:manage"
    LOCATION_MANAGE = "location:manage"
    RETENTION_MANAGE = "retention:manage"
    AUDIT_READ = "audit:read"
    USER_LIST_ASSIGNABLE = "user:list_assignable"

    CASE_CREATE = "case:create"
    CASE_READ = "case:read"
    CASE_UPDATE = "case:update"
    CASE_SEARCH_ALL_STATIONS = "case:search_all_stations"
    CASE_ASSIGN = "case:assign"
    CASE_REVIEW = "case:review"  # move a case to UNDER_REVIEW
    CASE_RESOLVE = "case:resolve"  # MATCH_FOUND, RESOLVED, CLOSED, REOPENED
    CUSTODY_UPDATE = "case:custody_update"
    CONTACT_READ = "case:contact_read"

    NOTE_CREATE = "note:create"
    NOTE_SENSITIVE = "note:sensitive"

    ATTACHMENT_UPLOAD = "attachment:upload"
    ATTACHMENT_DOWNLOAD = "attachment:download"
    ATTACHMENT_DELETE = "attachment:delete"

    MATCH_PROPOSE = "match:propose"
    MATCH_REVIEW = "match:review"

    NOTIFICATION_SEND = "notification:send"


_HELP_DESK = frozenset(
    {
        Permission.CASE_CREATE,
        Permission.CASE_READ,
        Permission.CASE_UPDATE,
        Permission.CASE_REVIEW,
        Permission.NOTE_CREATE,
        Permission.ATTACHMENT_UPLOAD,
        Permission.ATTACHMENT_DOWNLOAD,
        Permission.MATCH_PROPOSE,
        Permission.NOTIFICATION_SEND,
    }
)

_POLICE = _HELP_DESK | frozenset(
    {
        Permission.USER_LIST_ASSIGNABLE,
        Permission.CASE_SEARCH_ALL_STATIONS,
        Permission.CASE_ASSIGN,
        Permission.CASE_RESOLVE,
        Permission.CUSTODY_UPDATE,
        Permission.CONTACT_READ,
        Permission.NOTE_SENSITIVE,
        Permission.ATTACHMENT_DELETE,
        Permission.MATCH_REVIEW,
    }
)

_ADMIN = _POLICE | frozenset(
    {Permission.USER_MANAGE, Permission.STATION_MANAGE, Permission.LOCATION_MANAGE, Permission.RETENTION_MANAGE, Permission.AUDIT_READ}
)

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.HELP_DESK_OPERATOR: _HELP_DESK,
    Role.POLICE_OFFICER: _POLICE,
    Role.ADMIN: _ADMIN,
}


def has_permission(user: User, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS.get(Role(user.role), frozenset())


def require_permission(user: User, permission: Permission) -> None:
    if not has_permission(user, permission):
        raise Forbidden("You do not have permission to perform this action", code="permission_denied")
