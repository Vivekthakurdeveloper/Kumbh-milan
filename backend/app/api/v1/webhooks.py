"""Inbound provider webhooks. No bearer token: each provider authenticates with its own shared secret."""

import hmac
import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, Header, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.responses import errors
from app.auth.dependencies import DbSession
from app.common.errors import ServiceUnavailable, Unauthenticated, ValidationFailed
from app.config import get_settings
from app.database import SessionLocal
from app.services import chatbot as chatbot_service
from app.services import integrations as integration_service
from app.services import notifications as notification_service

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])
MAX_RECEIPTS = 500


def _receipts(payload: Any) -> list[tuple[str, str | None]]:
    """Accepts MSG91's flat and nested report shapes: [{requestId, status|desc}] or {data: [{requestId, report: [...]}]}."""
    entries = payload.get("data", payload) if isinstance(payload, dict) else payload
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        raise ValidationFailed("Unrecognised receipt payload", code="invalid_receipt")
    receipts: list[tuple[str, str | None]] = []
    for entry in entries[:MAX_RECEIPTS]:
        if not isinstance(entry, dict):
            continue
        request_id = entry.get("requestId") or entry.get("request_id")
        if not request_id:
            continue
        reports = entry.get("report") if isinstance(entry.get("report"), list) else [entry]
        for report in reports:
            if isinstance(report, dict):
                code = report.get("desc") or report.get("status")
                receipts.append((str(request_id)[:100], None if code is None else str(code)))
    return receipts


@router.post("/msg91/delivery", responses=errors(401, 422, 503),
             summary="MSG91 delivery receipts (authenticated with X-Webhook-Token)")
def msg91_delivery(
    db: DbSession,
    payload: Annotated[Any, Body()],
    webhook_token: Annotated[str | None, Header(alias="X-Webhook-Token")] = None,
) -> dict[str, Any]:
    secret = get_settings().msg91_webhook_secret
    if not secret:
        raise ServiceUnavailable("Delivery receipts are not configured", code="webhook_unconfigured")
    if not webhook_token or not hmac.compare_digest(webhook_token.encode(), secret.encode()):
        raise Unauthenticated("Invalid webhook token", code="invalid_webhook_token")
    outcomes: dict[str, int] = {}
    for request_id, code in _receipts(payload):
        outcome = notification_service.apply_delivery_receipt(
            db, provider="msg91", provider_message_id=request_id, provider_status=code
        )
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    return {"processed": sum(outcomes.values()), "outcomes": outcomes}


class WhatsAppInbound(BaseModel):
    """Normalised inbound message; the gateway adapter maps its provider's payload onto this shape."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    message_id: str = Field(min_length=1, max_length=128)
    sender: str = Field(alias="from", min_length=6, max_length=20)
    text: str = Field(min_length=1, max_length=4000)


class WhatsAppReply(BaseModel):
    session_id: uuid.UUID
    reply: str
    intent: str
    handoff: bool
    duplicate: bool


@router.post("/whatsapp/inbound", response_model=WhatsAppReply, responses=errors(401, 422, 503),
             summary="Inbound WhatsApp message to the chatbot; the gateway sends the returned reply")
def whatsapp_inbound(
    db: DbSession,
    payload: WhatsAppInbound,
    webhook_token: Annotated[str | None, Header(alias="X-Webhook-Token")] = None,
) -> WhatsAppReply:
    secret = get_settings().whatsapp_webhook_secret
    if not secret:
        raise ServiceUnavailable("WhatsApp chat is not configured", code="webhook_unconfigured")
    if not webhook_token or not hmac.compare_digest(webhook_token.encode(), secret.encode()):
        raise Unauthenticated("Invalid webhook token", code="invalid_webhook_token")
    reply, duplicate = chatbot_service.whatsapp_inbound(
        db, sender=payload.sender, message_id=payload.message_id, text=payload.text
    )
    return WhatsAppReply(session_id=reply.session.id, reply=reply.text, intent=reply.intent,
                         handoff=reply.handoff, duplicate=duplicate)


@router.post("/integrations/{code}", status_code=202, responses=errors(401, 422),
             summary="Signed inbound message from an external system (quarantined for staff review)")
async def integration_inbound(
    code: str,
    request: Request,
    timestamp: Annotated[str | None, Header(alias="X-Kumbh-Timestamp")] = None,
    signature: Annotated[str | None, Header(alias="X-Kumbh-Signature")] = None,
) -> dict[str, Any]:
    body = await request.body()
    if len(body) > 256 * 1024:
        raise ValidationFailed("Payload too large", code="payload_too_large")
    try:
        payload = json.loads(body or b"{}")
    except json.JSONDecodeError:
        raise ValidationFailed("Body must be JSON", code="invalid_inbound") from None
    if not isinstance(payload, dict):
        raise ValidationFailed("Body must be a JSON object", code="invalid_inbound")
    with SessionLocal() as db:
        row, duplicate = integration_service.receive_inbound(db, code, body, payload, timestamp, signature)
        return {"id": str(row.id), "status": row.status, "duplicate": duplicate}
