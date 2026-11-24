from enum import StrEnum

from sqlalchemy import Enum as SAEnum


class Role(StrEnum):
    ADMIN = "ADMIN"
    POLICE_OFFICER = "POLICE_OFFICER"
    HELP_DESK_OPERATOR = "HELP_DESK_OPERATOR"
    PUBLIC = "PUBLIC"


ROLE_LABELS: dict[Role, str] = {
    Role.ADMIN: "Administrator",
    Role.POLICE_OFFICER: "Police Officer",
    Role.HELP_DESK_OPERATOR: "Help Desk",
    Role.PUBLIC: "Citizen",
}


def role_label(role: Role | str) -> str:
    """Canonical human-readable role label for every API response."""
    return ROLE_LABELS[Role(role)]


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


class CaseSource(StrEnum):
    STAFF = "STAFF"
    PUBLIC = "PUBLIC"


class PublicReviewStatus(StrEnum):
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


class OtpPurpose(StrEnum):
    REPORT_MISSING = "REPORT_MISSING"
    REPORT_SIGHTING = "REPORT_SIGHTING"
    CASE_STATUS = "CASE_STATUS"
    GRIEVANCE = "GRIEVANCE"


class SightingStatus(StrEnum):
    UNVERIFIED = "UNVERIFIED"
    REVIEWED = "REVIEWED"
    DISMISSED = "DISMISSED"


class PushPlatform(StrEnum):
    ANDROID = "ANDROID"
    IOS = "IOS"


class SyncTombstoneReason(StrEnum):
    ARCHIVED = "ARCHIVED"
    SCOPE_REVOKED = "SCOPE_REVOKED"


class AttachmentUploadSessionStatus(StrEnum):
    OPEN = "OPEN"
    READY = "READY"
    FINALIZED = "FINALIZED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"
    EXPIRED = "EXPIRED"


class PublicInformationKind(StrEnum):
    HELP_DESK = "HELP_DESK"
    EMERGENCY_CONTACT = "EMERGENCY_CONTACT"
    INSTRUCTION = "INSTRUCTION"
    ANNOUNCEMENT = "ANNOUNCEMENT"


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


class HandoverKind(StrEnum):
    PERSON_REUNIFICATION = "PERSON_REUNIFICATION"
    ITEM_RETURN = "ITEM_RETURN"


class HandoverStatus(StrEnum):
    CLAIMANT_RECORDED = "CLAIMANT_RECORDED"
    VERIFIED = "VERIFIED"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"


class IdentityVerificationMethod(StrEnum):
    GOVERNMENT_ID = "GOVERNMENT_ID"
    FAMILY_CORROBORATION = "FAMILY_CORROBORATION"
    PHOTO_OR_MARKS = "PHOTO_OR_MARKS"
    OTHER = "OTHER"


class IdentityDocumentType(StrEnum):
    AADHAAR = "AADHAAR"
    VOTER_ID = "VOTER_ID"
    PASSPORT = "PASSPORT"
    DRIVING_LICENCE = "DRIVING_LICENCE"
    OTHER = "OTHER"


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
    ANONYMIZE_CLOSED_GRIEVANCES = "ANONYMIZE_CLOSED_GRIEVANCES"


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
    CASE_SUBMITTED = "CASE_SUBMITTED"
    CASE_APPROVED = "CASE_APPROVED"
    CASE_STATUS_UPDATED = "CASE_STATUS_UPDATED"
    POTENTIAL_MATCH_VERIFIED = "POTENTIAL_MATCH_VERIFIED"
    CASE_REUNITED = "CASE_REUNITED"
    CASE_CLOSED = "CASE_CLOSED"
    OTP_CODE = "OTP_CODE"
    PASSWORD_RESET = "PASSWORD_RESET"


class NotificationChannel(StrEnum):
    SMS = "SMS"
    WHATSAPP = "WHATSAPP"
    EMAIL = "EMAIL"
    PUSH = "PUSH"


class NotificationStatus(StrEnum):
    PENDING = "PENDING"
    SENDING = "SENDING"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
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
    CASE_ASSIGNED = "CASE_ASSIGNED"
    ASSIGNMENT_APPROVAL_REQUESTED = "ASSIGNMENT_APPROVAL_REQUESTED"
    ASSIGNMENT_DECIDED = "ASSIGNMENT_DECIDED"
    GRIEVANCE_ASSIGNED = "GRIEVANCE_ASSIGNED"
    GRIEVANCE_ESCALATED = "GRIEVANCE_ESCALATED"
    REPORT_READY = "REPORT_READY"
    CHAT_HANDOFF = "CHAT_HANDOFF"


class AssignmentRequestStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class GrievanceCategory(StrEnum):
    SERVICE = "SERVICE"
    STAFF_CONDUCT = "STAFF_CONDUCT"
    DELAY = "DELAY"
    OTHER = "OTHER"


class GrievanceStatus(StrEnum):
    OPEN = "OPEN"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"
    REOPENED = "REOPENED"


class GrievanceEventKind(StrEnum):
    CREATED = "CREATED"
    STATUS_CHANGED = "STATUS_CHANGED"
    ASSIGNED = "ASSIGNED"
    COMMENT = "COMMENT"
    ESCALATED = "ESCALATED"


class ReportType(StrEnum):
    CASES = "CASES"
    REUNIFICATIONS = "REUNIFICATIONS"
    CUSTODY = "CUSTODY"
    GRIEVANCES = "GRIEVANCES"
    NOTIFICATIONS = "NOTIFICATIONS"


class ReportJobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class ReportCadence(StrEnum):
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"


class ReportFormat(StrEnum):
    CSV = "CSV"
    PDF = "PDF"


class IntegrationKind(StrEnum):
    PUBLIC_ADDRESS = "PUBLIC_ADDRESS"
    DISPLAY = "DISPLAY"
    SOCIAL = "SOCIAL"
    GOVERNMENT = "GOVERNMENT"


class IntegrationAdapter(StrEnum):
    MOCK = "MOCK"
    WEBHOOK = "WEBHOOK"


class IntegrationHealth(StrEnum):
    UNKNOWN = "UNKNOWN"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"


class AlertStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"
    WITHDRAWN = "WITHDRAWN"
    EXPIRED = "EXPIRED"


class DispatchOperation(StrEnum):
    PUBLISH = "PUBLISH"
    WITHDRAW = "WITHDRAW"
    CASE_EXPORT = "CASE_EXPORT"


class DispatchStatus(StrEnum):
    PENDING = "PENDING"
    SENDING = "SENDING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class InboundMessageStatus(StrEnum):
    QUARANTINED = "QUARANTINED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class ChatChannel(StrEnum):
    WEB = "WEB"
    APP = "APP"
    WHATSAPP = "WHATSAPP"


class ChatSessionStatus(StrEnum):
    OPEN = "OPEN"
    HANDED_OFF = "HANDED_OFF"
    CLOSED = "CLOSED"


class ChatRole(StrEnum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"
    STAFF = "STAFF"


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
