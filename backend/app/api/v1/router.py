from fastapi import APIRouter

from app.api.v1 import (
    admin,
    assignments,
    assistant,
    audit,
    auth,
    cases,
    chat,
    dashboard,
    devices,
    events,
    grievances,
    integrations,
    locations,
    matches,
    media,
    public,
    reference,
    reports,
    retention,
    staff_notifications,
    sync,
    webhooks,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(admin.stations)
api_router.include_router(admin.users)
api_router.include_router(locations.router)
api_router.include_router(public.router)
api_router.include_router(public.admin_router)
api_router.include_router(public.sightings_router)
api_router.include_router(devices.router)
api_router.include_router(grievances.public_router)
api_router.include_router(chat.public_router)
# Type-specific and fixed /cases/... routes must be registered before /cases/{case_id}.
api_router.include_router(assignments.router)
for router in cases.type_routers:
    api_router.include_router(router)
api_router.include_router(cases.shared)
api_router.include_router(matches.router)
api_router.include_router(media.router)
api_router.include_router(staff_notifications.router)
api_router.include_router(sync.router)
api_router.include_router(audit.router)
api_router.include_router(retention.router)
api_router.include_router(dashboard.router)
api_router.include_router(reports.router)
api_router.include_router(reference.router)
api_router.include_router(grievances.router)
api_router.include_router(events.router)
api_router.include_router(integrations.router)
api_router.include_router(integrations.alerts)
api_router.include_router(chat.router)
api_router.include_router(assistant.router)
api_router.include_router(webhooks.router)
