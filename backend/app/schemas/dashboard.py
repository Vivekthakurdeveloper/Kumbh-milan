import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ReadModel


class DashboardSummary(ReadModel):
    """
    Counts cover only cases the caller may see, excluding archived cases.

    With `from` and/or `to`, urgent_active, unassigned_active, registered_today and pending_match_reviews are
    limited to cases registered (and matches proposed) inside that window; period_from and period_to echo it.
    """

    generated_at: datetime
    period_from: datetime | None = Field(None, description="Start of the applied window; null when no window was requested")
    period_to: datetime | None = Field(None, description="End of the applied window; null when open-ended or not requested")
    active_by_type: dict[str, int] = Field(description="Active cases per case type")
    by_status: dict[str, int]
    active_total: int
    urgent_active: int = Field(
        description="Active cases with CRITICAL or HIGH urgency; with a window, only those registered in it"
    )
    unassigned_active: int = Field(description="Active unassigned cases; with a window, only those registered in it")
    registered_today: int = Field(
        description="Cases registered since midnight Asia/Kolkata; with a window, cases registered inside it"
    )
    pending_match_reviews: int = Field(
        description="PROPOSED matches (manual or automatic) touching visible cases; with a window, proposed inside it"
    )
    attachments_awaiting_scan: int
    notifications_failed: int


class MedianDurations(BaseModel):
    to_assignment: float | None
    to_confirmed_match: float | None
    to_reunification_or_return: float | None
    to_closure: float | None


class RecentActivity(BaseModel):
    type: str
    case_id: uuid.UUID | None
    case_number: str | None
    occurred_at: datetime


class DashboardAnalytics(BaseModel):
    generated_at: datetime
    period_from: datetime
    period_to: datetime
    registered_total: int
    by_station: dict[str, int]
    by_case_type: dict[str, int]
    by_urgency: dict[str, int]
    by_status: dict[str, int]
    by_sector: dict[str, int] = Field(description="Sector location id (or UNLINKED) -> cases registered")
    by_day: dict[str, int] = Field(description="Asia/Kolkata calendar day -> cases registered")
    median_seconds: MedianDurations
    unresolved_age: dict[str, int] = Field(description="Active cases by time since registration")
    resolved_in_period: int
    notifications_failed: int
    grievances_open: int
    grievances_overdue: int
    recent_activity: list[RecentActivity]
