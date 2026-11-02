from datetime import datetime

from pydantic import Field

from app.schemas.common import ReadModel


class DashboardSummary(ReadModel):
    """Counts cover only cases the caller may see, excluding archived cases."""

    generated_at: datetime
    active_by_type: dict[str, int] = Field(description="Active cases per case type")
    by_status: dict[str, int]
    active_total: int
    urgent_active: int = Field(description="Active cases with CRITICAL or HIGH urgency")
    unassigned_active: int
    registered_today: int = Field(description="Since midnight Asia/Kolkata")
    pending_match_reviews: int = Field(description="PROPOSED matches (manual or automatic) touching visible cases")
    attachments_awaiting_scan: int
    notifications_failed: int
