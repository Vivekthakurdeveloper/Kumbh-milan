from datetime import datetime

from pydantic import Field

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
