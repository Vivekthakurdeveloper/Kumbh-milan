from enum import StrEnum

from sqlalchemy import Enum as SAEnum


class Role(StrEnum):
    ADMIN = "ADMIN"
    POLICE_OFFICER = "POLICE_OFFICER"
    HELP_DESK_OPERATOR = "HELP_DESK_OPERATOR"


class StationKind(StrEnum):
    POLICE_STATION = "POLICE_STATION"
    HELP_DESK = "HELP_DESK"


class CaseType(StrEnum):
    MISSING_PERSON = "MISSING_PERSON"
    FOUND_PERSON = "FOUND_PERSON"
    LOST_ITEM = "LOST_ITEM"
    FOUND_ITEM = "FOUND_ITEM"


class CaseStatus(StrEnum):
    OPEN = "OPEN"
    UNDER_REVIEW = "UNDER_REVIEW"
    ASSIGNED = "ASSIGNED"
    MATCH_FOUND = "MATCH_FOUND"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"
    REOPENED = "REOPENED"


class UrgencyLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Gender(StrEnum):
    MALE = "MALE"
    FEMALE = "FEMALE"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class AgeGroup(StrEnum):
    INFANT = "INFANT"
    CHILD = "CHILD"
    ADULT = "ADULT"
    ELDERLY = "ELDERLY"
    UNKNOWN = "UNKNOWN"


class CommunicationStatus(StrEnum):
    VERBAL = "VERBAL"
    LIMITED = "LIMITED"
    NON_VERBAL = "NON_VERBAL"
    UNCONSCIOUS = "UNCONSCIOUS"
    UNKNOWN = "UNKNOWN"


class CustodyStatus(StrEnum):
    IN_CUSTODY = "IN_CUSTODY"
    TRANSFERRED = "TRANSFERRED"
    RELEASED = "RELEASED"


class NoteType(StrEnum):
    OPERATIONAL = "OPERATIONAL"
    FOLLOW_UP = "FOLLOW_UP"
    CORRECTION = "CORRECTION"


class MediaType(StrEnum):
    PHOTO = "PHOTO"
    AUDIO = "AUDIO"


class LocationKind(StrEnum):
    GHAT = "GHAT"
    SECTOR = "SECTOR"
    PARKING = "PARKING"
    CAMP = "CAMP"
    HELP_DESK = "HELP_DESK"
    LANDMARK = "LANDMARK"


class CoordinateSource(StrEnum):
    """How a case's own coordinates were captured. Coordinates are never inferred from free text."""

    DEVICE_GPS = "DEVICE_GPS"
    MAP_PIN = "MAP_PIN"


class RetentionPolicy(StrEnum):
    ARCHIVE_CLOSED_CASES = "ARCHIVE_CLOSED_CASES"
    ANONYMIZE_ARCHIVED_CASES = "ANONYMIZE_ARCHIVED_CASES"
    PURGE_ATTACHMENT_OBJECTS = "PURGE_ATTACHMENT_OBJECTS"


class RetentionRunStatus(StrEnum):
    PLANNED = "PLANNED"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class NotificationTemplate(StrEnum):
    CASE_REGISTERED = "CASE_REGISTERED"
    CASE_UPDATE_VISIT_DESK = "CASE_UPDATE_VISIT_DESK"


class NotificationChannel(StrEnum):
    SMS = "SMS"
    WHATSAPP = "WHATSAPP"


class NotificationStatus(StrEnum):
    PENDING = "PENDING"
    SENDING = "SENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ScanStatus(StrEnum):
    """QUARANTINED -> SCANNING -> CLEAN | REJECTED, with SCAN_FAILED after retries are exhausted."""

    QUARANTINED = "QUARANTINED"
    SCANNING = "SCANNING"
    CLEAN = "CLEAN"
    REJECTED = "REJECTED"
    SCAN_FAILED = "SCAN_FAILED"
    SKIPPED_DEV = "SKIPPED_DEV"  # development mode without a scanner; never downloadable in staging/production


class MatchDomain(StrEnum):
    PERSON = "PERSON"
    ITEM = "ITEM"


class MatchStatus(StrEnum):
    PROPOSED = "PROPOSED"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    REVOKED = "REVOKED"


class MatchSource(StrEnum):
    MANUAL = "MANUAL"
    AUTOMATIC = "AUTOMATIC"
    FACE = "FACE"


class ConfidenceBand(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class FaceStatus(StrEnum):
    FACE_FOUND = "FACE_FOUND"
    NO_FACE = "NO_FACE"
    MULTIPLE_FACES = "MULTIPLE_FACES"
    UNREADABLE = "UNREADABLE"


class StaffNotificationKind(StrEnum):
    MATCH_CANDIDATE = "MATCH_CANDIDATE"


class StaffNotificationStatus(StrEnum):
    UNREAD = "UNREAD"
    READ = "READ"


class MatchReviewStage(StrEnum):
    AWAITING_FIRST_APPROVAL = "AWAITING_FIRST_APPROVAL"
    AWAITING_SECOND_APPROVAL = "AWAITING_SECOND_APPROVAL"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    REVOKED = "REVOKED"


def enum_type(enum_cls: type[StrEnum], name: str) -> SAEnum:
    """VARCHAR + CHECK constraint, which avoids PostgreSQL enum-type migrations when values change."""
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
    )
