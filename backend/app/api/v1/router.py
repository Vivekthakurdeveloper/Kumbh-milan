from fastapi import APIRouter

from app.api.v1 import (
    admin,
    audit,
    auth,
    cases,
    dashboard,
    locations,
    matches,
    reports,
    retention,
    staff_notifications,
    sync,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(admin.stations)
api_router.include_router(admin.users)
api_router.include_router(locations.router)
# Type-specific routes must be registered before /cases/{case_id}.
for router in cases.type_routers:
    api_router.include_router(router)
api_router.include_router(cases.shared)
api_router.include_router(matches.router)
api_router.include_router(staff_notifications.router)
api_router.include_router(sync.router)
api_router.include_router(audit.router)
api_router.include_router(retention.router)
api_router.include_router(dashboard.router)
api_router.include_router(reports.router)
