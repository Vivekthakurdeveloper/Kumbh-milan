"""External channels: destinations, public alerts (PA / LED-VMD / social), government exports, inbound review."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.v1.responses import AUTHED, errors
from app.auth.dependencies import CurrentPrincipal, DbSession
from app.models.enums import AlertStatus, DispatchStatus, InboundMessageStatus, IntegrationKind
from app.schemas.common import Page, PageParams
from app.schemas.integrations import (
    AlertCreate,
    AlertRead,
    AlertUpdate,
    AlertWithdraw,
    CaseExportRequest,
    DestinationCreate,
    DestinationRead,
    DestinationUpdate,
    DispatchRead,
    InboundRead,
    InboundReview,
)
from app.services import integrations as integration_service

router = APIRouter(prefix="/integrations", tags=["Integrations"])
alerts = APIRouter(prefix="/alerts", tags=["Public alerts"])
PageDep = Annotated[PageParams, Depends()]
STATE = {**AUTHED, **errors(404, 409)}


def _destination(row) -> DestinationRead:
    return DestinationRead.model_validate(row).model_copy(update={"has_secret": bool(row.secret_ciphertext)})


@router.get("/destinations", response_model=list[DestinationRead], responses=AUTHED)
def list_destinations(principal: CurrentPrincipal, db: DbSession, kind: IntegrationKind | None = None):
    return [_destination(r) for r in integration_service.list_destinations(db, principal.user, kind)]


@router.post("/destinations", response_model=DestinationRead, status_code=status.HTTP_201_CREATED,
             responses={**AUTHED, **errors(409)}, summary="Register an allowlisted destination (administrators)")
def create_destination(payload: DestinationCreate, principal: CurrentPrincipal, db: DbSession):
    return _destination(integration_service.create_destination(db, principal.user, payload.model_dump()))


@router.patch("/destinations/{destination_id}", response_model=DestinationRead, responses=STATE)
def update_destination(destination_id: uuid.UUID, payload: DestinationUpdate, principal: CurrentPrincipal,
                       db: DbSession):
    changes = payload.model_dump(exclude_unset=True)
    return _destination(integration_service.update_destination(db, principal.user, destination_id, changes))


@router.get("/destinations/{destination_id}/active-alerts", response_model=list[AlertRead], responses=STATE,
            summary="Alerts currently live on this PA zone, display, or account")
def active_alerts(destination_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    return integration_service.active_alerts_for(db, principal.user, destination_id)


@router.post("/destinations/{destination_id}/case-exports", response_model=DispatchRead,
             status_code=status.HTTP_202_ACCEPTED, responses=STATE,
             summary="Queue a minimal case summary for a government system (legal basis required)")
def export_case(destination_id: uuid.UUID, payload: CaseExportRequest, principal: CurrentPrincipal, db: DbSession):
    return integration_service.export_case(db, principal.user, destination_id, payload.case_id, payload.legal_basis)


@router.get("/dispatches", response_model=Page[DispatchRead], responses=AUTHED,
            summary="Delivery status for every destination (payloads are not returned)")
def list_dispatches(principal: CurrentPrincipal, db: DbSession, page: PageDep, status_: Annotated[DispatchStatus | None, Query(alias="status")] = None,
                    destination_id: uuid.UUID | None = None):
    rows, total = integration_service.list_dispatches(
        db, principal.user, status=status_, destination_id=destination_id, limit=page.limit, offset=page.offset
    )
    return Page(items=[DispatchRead.model_validate(r) for r in rows], total=total, limit=page.limit,
                offset=page.offset)


@router.post("/dispatches/{dispatch_id}/retry", response_model=DispatchRead, responses=STATE)
def retry_dispatch(dispatch_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    return integration_service.retry_dispatch(db, principal.user, dispatch_id)


@router.get("/inbound", response_model=Page[InboundRead], responses=AUTHED,
            summary="Inbound messages from external systems, quarantined until reviewed")
def list_inbound(principal: CurrentPrincipal, db: DbSession, page: PageDep,
                 status_: Annotated[InboundMessageStatus | None, Query(alias="status")] = None):
    rows, total = integration_service.list_inbound(db, principal.user, status_, limit=page.limit, offset=page.offset)
    return Page(items=[InboundRead.model_validate(r) for r in rows], total=total, limit=page.limit,
                offset=page.offset)


@router.post("/inbound/{message_id}/review", response_model=InboundRead, responses=STATE)
def review_inbound(message_id: uuid.UUID, payload: InboundReview, principal: CurrentPrincipal, db: DbSession):
    return integration_service.review_inbound(db, principal.user, message_id, payload.accept, payload.note)


@alerts.get("", response_model=Page[AlertRead], responses=AUTHED)
def list_alerts(principal: CurrentPrincipal, db: DbSession, page: PageDep, status_: Annotated[AlertStatus | None, Query(alias="status")] = None):
    rows, total = integration_service.list_alerts(db, principal.user, status_, limit=page.limit, offset=page.offset)
    return Page(items=[AlertRead.model_validate(r) for r in rows], total=total, limit=page.limit, offset=page.offset)


@alerts.post("", response_model=AlertRead, status_code=status.HTTP_201_CREATED, responses=AUTHED,
             summary="Draft a bilingual public alert (no phone numbers, emails, or ID numbers)")
def create_alert(payload: AlertCreate, principal: CurrentPrincipal, db: DbSession):
    return integration_service.create_alert(db, principal.user, payload.model_dump())


@alerts.get("/{alert_id}", response_model=AlertRead, responses={**AUTHED, **errors(404)})
def get_alert(alert_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    return integration_service.get_alert(db, principal.user, alert_id)


@alerts.patch("/{alert_id}", response_model=AlertRead, responses=STATE)
def update_alert(alert_id: uuid.UUID, payload: AlertUpdate, principal: CurrentPrincipal, db: DbSession):
    return integration_service.update_alert(db, principal.user, alert_id, payload.model_dump(exclude_unset=True))


@alerts.post("/{alert_id}/approve", response_model=AlertRead, responses=STATE,
             summary="Second-person approval; queues dispatch to every selected destination")
def approve_alert(alert_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    return integration_service.approve_alert(db, principal.user, alert_id)


@alerts.post("/{alert_id}/withdraw", response_model=AlertRead, responses=STATE,
             summary="Withdraw an alert; delivered destinations receive a withdrawal")
def withdraw_alert(alert_id: uuid.UUID, payload: AlertWithdraw, principal: CurrentPrincipal, db: DbSession):
    return integration_service.withdraw_alert(db, principal.user, alert_id, payload.reason)


@alerts.get("/{alert_id}/dispatches", response_model=list[DispatchRead], responses={**AUTHED, **errors(404)})
def alert_dispatches(alert_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    return integration_service.alert_dispatches(db, principal.user, alert_id)
