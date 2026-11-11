"""
Notifications to families and owners.

Templates carry only the case number and the station name (plus a generic OTP placeholder for
citizen verification). Delivery goes through a provider interface. Live MSG91/SMTP/FCM delivery
requires credentials and approvals and remains unverified until those are supplied.
"""

from __future__ import annotations

import json
import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from functools import lru_cache
from typing import Protocol
from urllib import error, request

from app.config import get_settings
from app.models.enums import NotificationChannel, NotificationTemplate
from app.notifications.masking import mask, mask_email, normalise_phone

logger = logging.getLogger("kumbh_setu.notifications")

TEMPLATES: dict[NotificationTemplate, dict[str, str]] = {
    NotificationTemplate.CASE_REGISTERED: {
        "en": "Kumbh Setu: report {case_number} is registered at {station}. Keep this number for enquiries.",
        "hi": "कुंभ सेतु: आपकी रिपोर्ट {case_number} {station} पर दर्ज है। पूछताछ के लिए यह नंबर संभालकर रखें।",
    },
    NotificationTemplate.CASE_UPDATE_VISIT_DESK: {
        "en": "Kumbh Setu: there is an update on report {case_number}. Please visit {station} with photo ID.",
        "hi": "कुंभ सेतु: रिपोर्ट {case_number} पर नई जानकारी है। कृपया फोटो पहचान पत्र के साथ {station} पर आएं।",
    },
    NotificationTemplate.CASE_SUBMITTED: {
        "en": "Kumbh Setu: report {case_number} was submitted and is pending staff verification at {station}.",
        "hi": "कुंभ सेतु: रिपोर्ट {case_number} दर्ज हो गई है और {station} पर कर्मचारी सत्यापन की प्रतीक्षा में है।",
    },
    NotificationTemplate.CASE_APPROVED: {
        "en": "Kumbh Setu: report {case_number} has been accepted by staff at {station}.",
        "hi": "कुंभ सेतु: रिपोर्ट {case_number} को {station} के कर्मचारियों ने स्वीकार कर लिया है।",
    },
    NotificationTemplate.CASE_STATUS_UPDATED: {
        "en": "Kumbh Setu: the status of report {case_number} was updated. Visit {station} for details.",
        "hi": "कुंभ सेतु: रिपोर्ट {case_number} की स्थिति अपडेट हुई है। विवरण के लिए {station} पर आएं।",
    },
    NotificationTemplate.POTENTIAL_MATCH_VERIFIED: {
        "en": "Kumbh Setu: staff verified a potential match for report {case_number}. Please visit {station}.",
        "hi": "कुंभ सेतु: रिपोर्ट {case_number} के संभावित मिलान की कर्मचारी पुष्टि हुई है। कृपया {station} पर आएं।",
    },
    NotificationTemplate.CASE_REUNITED: {
        "en": "Kumbh Setu: report {case_number} is marked reunited. Thank you for coordinating with {station}.",
        "hi": "कुंभ सेतु: रिपोर्ट {case_number} को पुनर्मिलन के रूप में चिह्नित किया गया है। {station} से सहयोग के लिए धन्यवाद।",
    },
    NotificationTemplate.CASE_CLOSED: {
        "en": "Kumbh Setu: report {case_number} is now closed at {station}.",
        "hi": "कुंभ सेतु: रिपोर्ट {case_number} अब {station} पर बंद है।",
    },
    NotificationTemplate.OTP_CODE: {
        "en": "Kumbh Setu verification code: {otp}. Valid for a limited time. Do not share this code.",
        "hi": "कुंभ सेतु सत्यापन कोड: {otp}। सीमित समय के लिए मान्य। इस कोड को साझा न करें।",
    },
    NotificationTemplate.PASSWORD_RESET: {
        "en": "Kumbh Setu password reset code: {otp}. Valid for a limited time. Do not share this code.",
        "hi": "कुंभ सेतु पासवर्ड रीसेट कोड: {otp}। सीमित समय के लिए मान्य। इस कोड को साझा न करें।",
    },
}

EMAIL_SUBJECTS: dict[NotificationTemplate, dict[str, str]] = {
    NotificationTemplate.CASE_SUBMITTED: {
        "en": "Report {case_number} submitted",
        "hi": "रिपोर्ट {case_number} दर्ज",
    },
    NotificationTemplate.CASE_APPROVED: {
        "en": "Report {case_number} accepted",
        "hi": "रिपोर्ट {case_number} स्वीकृत",
    },
    NotificationTemplate.CASE_STATUS_UPDATED: {
        "en": "Update on report {case_number}",
        "hi": "रिपोर्ट {case_number} अपडेट",
    },
    NotificationTemplate.CASE_CLOSED: {
        "en": "Report {case_number} closed",
        "hi": "रिपोर्ट {case_number} बंद",
    },
    NotificationTemplate.CASE_REUNITED: {
        "en": "Report {case_number} reunited",
        "hi": "रिपोर्ट {case_number} पुनर्मिलन",
    },
    NotificationTemplate.CASE_REGISTERED: {
        "en": "Report {case_number} registered",
        "hi": "रिपोर्ट {case_number} पंजीकृत",
    },
    NotificationTemplate.CASE_UPDATE_VISIT_DESK: {
        "en": "Please visit the help desk for {case_number}",
        "hi": "रिपोर्ट {case_number} हेतु हेल्प डेस्क आएं",
    },
    NotificationTemplate.POTENTIAL_MATCH_VERIFIED: {
        "en": "Potential match update for {case_number}",
        "hi": "रिपोर्ट {case_number} संभावित मिलान",
    },
    NotificationTemplate.OTP_CODE: {
        "en": "Kumbh Setu verification code",
        "hi": "कुंभ सेतु सत्यापन कोड",
    },
    NotificationTemplate.PASSWORD_RESET: {
        "en": "Kumbh Setu password reset",
        "hi": "कुंभ सेतु पासवर्ड रीसेट",
    },
}

LOCALES = ("en", "hi")


def render(
    template: NotificationTemplate,
    locale: str,
    *,
    case_number: str = "",
    station: str = "",
    otp: str = "",
) -> str:
    return TEMPLATES[template][locale].format(case_number=case_number, station=station, otp=otp)


def render_subject(template: NotificationTemplate, locale: str, *, case_number: str = "") -> str:
    subjects = EMAIL_SUBJECTS.get(template) or {
        "en": "Kumbh Setu notification",
        "hi": "कुंभ सेतु सूचना",
    }
    return subjects[locale].format(case_number=case_number)


class ProviderError(Exception):
    def __init__(self, message: str, *, permanent: bool) -> None:
        super().__init__(message)
        self.permanent = permanent


@dataclass(frozen=True)
class Delivery:
    provider_message_id: str


class NotificationProvider(Protocol):
    name: str
    enabled: bool

    def send(
        self,
        *,
        channel: NotificationChannel,
        to: str,
        body: str,
        idempotency_key: str,
        subject: str | None = None,
        template: NotificationTemplate | None = None,
        locale: str | None = None,
        extras: dict | None = None,
    ) -> Delivery: ...


class DisabledProvider:
    name = "disabled"
    enabled = False

    def send(self, **_) -> Delivery:  # pragma: no cover
        raise ProviderError("notification provider disabled", permanent=False)


class MockProvider:
    name = "mock"
    enabled = True

    def __init__(self) -> None:
        self.deliveries: list[dict[str, str]] = []
        self.fail: ProviderError | None = None

    def send(
        self,
        *,
        channel: NotificationChannel,
        to: str,
        body: str,
        idempotency_key: str,
        subject: str | None = None,
        template: NotificationTemplate | None = None,
        locale: str | None = None,
        extras: dict | None = None,
    ) -> Delivery:
        if self.fail is not None:
            raise self.fail
        masked = mask_email(to) if channel is NotificationChannel.EMAIL else (
            to[:12] + "…" if channel is NotificationChannel.PUSH else mask(to)
        )
        self.deliveries.append(
            {
                "channel": str(channel),
                "to": masked,
                "body": body,
                "key": idempotency_key,
                "subject": subject or "",
            }
        )
        return Delivery(provider_message_id=f"mock-{idempotency_key[:40]}")


class ConsoleProvider:
    name = "console"
    enabled = True

    def send(
        self,
        *,
        channel: NotificationChannel,
        to: str,
        body: str,
        idempotency_key: str,
        subject: str | None = None,
        template: NotificationTemplate | None = None,
        locale: str | None = None,
        extras: dict | None = None,
    ) -> Delivery:
        masked = mask_email(to) if channel is NotificationChannel.EMAIL else (
            "device-token" if channel is NotificationChannel.PUSH else mask(to)
        )
        logger.info(
            "Console notification",
            extra={"channel": channel, "to": masked, "body": body, "subject": subject},
        )
        return Delivery(provider_message_id=f"console-{idempotency_key[:40]}")


class Msg91Provider:
    """HTTP adapter for MSG91 transactional SMS. Live delivery remains unverified without credentials."""

    name = "msg91"
    enabled = True

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.msg91_auth_key:
            raise ProviderError("MSG91_AUTH_KEY is not configured", permanent=True)
        self.auth_key = settings.msg91_auth_key
        self.sender_id = settings.msg91_sender_id
        self.base_url = settings.msg91_base_url.rstrip("/")
        self.template_ids = settings.msg91_template_ids
        self.timeout = settings.msg91_timeout_seconds

    def send(
        self,
        *,
        channel: NotificationChannel,
        to: str,
        body: str,
        idempotency_key: str,
        subject: str | None = None,
        template: NotificationTemplate | None = None,
        locale: str | None = None,
        extras: dict | None = None,
    ) -> Delivery:
        if channel not in (NotificationChannel.SMS, NotificationChannel.WHATSAPP):
            raise ProviderError(f"MSG91 adapter does not support {channel}", permanent=True)
        template_id = None
        if template is not None:
            template_id = self.template_ids.get(template.value)
        if not template_id:
            raise ProviderError("MSG91 template id is not configured for this message", permanent=True)
        payload = {
            "template_id": template_id,
            "short_url": "0",
            "recipients": [{"mobiles": to.lstrip("+"), "var": body}],
        }
        if self.sender_id:
            payload["sender"] = self.sender_id
        extras = extras or {}
        if extras.get("otp"):
            payload["recipients"][0]["otp"] = extras["otp"]
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self.base_url}/flow/",
            data=data,
            headers={
                "authkey": self.auth_key,
                "content-type": "application/json",
                "accept": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                status = getattr(response, "status", 200)
        except error.HTTPError as exc:
            permanent = exc.code in (400, 401, 403, 404, 422)
            raise ProviderError(f"msg91_http_{exc.code}", permanent=permanent) from exc
        except error.URLError as exc:
            raise ProviderError("msg91_unreachable", permanent=False) from exc
        if status >= 400:
            raise ProviderError(f"msg91_http_{status}", permanent=status < 500)
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {}
        message_id = str(parsed.get("message") or parsed.get("request_id") or idempotency_key[:40])
        return Delivery(provider_message_id=f"msg91-{message_id}"[:128])


class SmtpProvider:
    name = "smtp"
    enabled = True

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.smtp_enabled or not settings.smtp_host or not settings.smtp_from_email:
            raise ProviderError("SMTP is not configured", permanent=True)
        self.host = settings.smtp_host
        self.port = settings.smtp_port
        self.username = settings.smtp_username
        self.password = settings.smtp_password
        self.use_tls = settings.smtp_use_tls
        self.from_email = settings.smtp_from_email
        self.from_name = settings.smtp_from_name
        self.timeout = settings.smtp_timeout_seconds

    def send(
        self,
        *,
        channel: NotificationChannel,
        to: str,
        body: str,
        idempotency_key: str,
        subject: str | None = None,
        template: NotificationTemplate | None = None,
        locale: str | None = None,
        extras: dict | None = None,
    ) -> Delivery:
        if channel is not NotificationChannel.EMAIL:
            raise ProviderError(f"SMTP adapter does not support {channel}", permanent=True)
        message = EmailMessage()
        message["From"] = f"{self.from_name} <{self.from_email}>"
        message["To"] = to
        message["Subject"] = subject or "Kumbh Setu"
        message["Message-ID"] = f"<{idempotency_key}@kumbh-setu>"
        message.set_content(body)
        try:
            if self.use_tls:
                with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
                    smtp.starttls(context=ssl.create_default_context())
                    if self.username:
                        smtp.login(self.username, self.password or "")
                    smtp.send_message(message)
            else:
                with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
                    if self.username:
                        smtp.login(self.username, self.password or "")
                    smtp.send_message(message)
        except smtplib.SMTPRecipientsRefused as exc:
            raise ProviderError("smtp_recipient_refused", permanent=True) from exc
        except smtplib.SMTPException as exc:
            raise ProviderError("smtp_error", permanent=False) from exc
        except OSError as exc:
            raise ProviderError("smtp_unreachable", permanent=False) from exc
        return Delivery(provider_message_id=f"smtp-{idempotency_key[:40]}")


class FcmProvider:
    """Firebase Cloud Messaging for Android and iOS (APNs configured in Firebase)."""

    name = "fcm"
    enabled = True

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.fcm_enabled:
            raise ProviderError("FCM is not enabled", permanent=True)
        try:
            import firebase_admin
            from firebase_admin import credentials, messaging
        except ImportError as exc:  # pragma: no cover - dependency optional until installed
            raise ProviderError("firebase_admin is not installed", permanent=True) from exc
        self._messaging = messaging
        if not firebase_admin._apps:
            if settings.fcm_credentials_json:
                cred = credentials.Certificate(json.loads(settings.fcm_credentials_json))
            elif settings.fcm_credentials_file:
                cred = credentials.Certificate(settings.fcm_credentials_file)
            else:
                raise ProviderError("FCM credentials are not configured", permanent=True)
            firebase_admin.initialize_app(cred)

    def send(
        self,
        *,
        channel: NotificationChannel,
        to: str,
        body: str,
        idempotency_key: str,
        subject: str | None = None,
        template: NotificationTemplate | None = None,
        locale: str | None = None,
        extras: dict | None = None,
    ) -> Delivery:
        if channel is not NotificationChannel.PUSH:
            raise ProviderError(f"FCM adapter does not support {channel}", permanent=True)
        message = self._messaging.Message(
            token=to,
            notification=self._messaging.Notification(title=subject or "Kumbh Setu", body=body),
            data={"idempotency_key": idempotency_key},
            android=self._messaging.AndroidConfig(priority="high"),
            apns=self._messaging.APNSConfig(
                payload=self._messaging.APNSPayload(aps=self._messaging.Aps(sound="default"))
            ),
        )
        try:
            message_id = self._messaging.send(message)
        except Exception as exc:  # noqa: BLE001
            text = str(exc).lower()
            permanent = any(token in text for token in ("unregistered", "invalid-argument", "not-found", "registration-token"))
            raise ProviderError("fcm_error", permanent=permanent) from exc
        return Delivery(provider_message_id=str(message_id)[:128])


class CompositeProvider:
    """Routes SMS/WhatsApp, email, and push to the configured adapters."""

    name = "composite"
    enabled = True

    def __init__(
        self,
        *,
        sms: NotificationProvider,
        email: NotificationProvider | None = None,
        push: NotificationProvider | None = None,
    ) -> None:
        self.sms = sms
        self.email = email
        self.push = push
        self.enabled = bool(sms.enabled or (email and email.enabled) or (push and push.enabled))

    def send(
        self,
        *,
        channel: NotificationChannel,
        to: str,
        body: str,
        idempotency_key: str,
        subject: str | None = None,
        template: NotificationTemplate | None = None,
        locale: str | None = None,
        extras: dict | None = None,
    ) -> Delivery:
        if channel in (NotificationChannel.SMS, NotificationChannel.WHATSAPP):
            if not self.sms.enabled:
                raise ProviderError("SMS provider disabled", permanent=False)
            return self.sms.send(
                channel=channel, to=to, body=body, idempotency_key=idempotency_key,
                subject=subject, template=template, locale=locale, extras=extras,
            )
        if channel is NotificationChannel.EMAIL:
            if self.email is None or not self.email.enabled:
                raise ProviderError("Email provider disabled", permanent=False)
            return self.email.send(
                channel=channel, to=to, body=body, idempotency_key=idempotency_key,
                subject=subject, template=template, locale=locale, extras=extras,
            )
        if channel is NotificationChannel.PUSH:
            if self.push is None or not self.push.enabled:
                raise ProviderError("Push provider disabled", permanent=False)
            return self.push.send(
                channel=channel, to=to, body=body, idempotency_key=idempotency_key,
                subject=subject, template=template, locale=locale, extras=extras,
            )
        raise ProviderError(f"Unsupported channel {channel}", permanent=True)


def _build_sms_provider() -> NotificationProvider:
    choice = get_settings().notification_provider
    if choice == "console":
        return ConsoleProvider()
    if choice == "mock":
        return MockProvider()
    if choice == "msg91":
        return Msg91Provider()
    return DisabledProvider()


def _build_email_provider() -> NotificationProvider | None:
    settings = get_settings()
    if settings.notification_provider == "mock":
        return MockProvider()
    if settings.notification_provider == "console":
        return ConsoleProvider()
    if settings.smtp_enabled:
        return SmtpProvider()
    return DisabledProvider()


def _build_push_provider() -> NotificationProvider | None:
    settings = get_settings()
    if settings.notification_provider == "mock":
        return MockProvider()
    if settings.notification_provider == "console":
        return ConsoleProvider()
    if settings.fcm_enabled:
        return FcmProvider()
    return DisabledProvider()


@lru_cache
def get_provider() -> NotificationProvider:
    return CompositeProvider(
        sms=_build_sms_provider(),
        email=_build_email_provider(),
        push=_build_push_provider(),
    )


def reset_provider_cache() -> None:
    get_provider.cache_clear()


# Re-export helpers expected by older imports.
__all__ = [
    "TEMPLATES",
    "EMAIL_SUBJECTS",
    "LOCALES",
    "Delivery",
    "ProviderError",
    "NotificationProvider",
    "DisabledProvider",
    "MockProvider",
    "ConsoleProvider",
    "Msg91Provider",
    "SmtpProvider",
    "FcmProvider",
    "CompositeProvider",
    "get_provider",
    "reset_provider_cache",
    "render",
    "render_subject",
    "normalise_phone",
    "mask",
    "mask_email",
]
