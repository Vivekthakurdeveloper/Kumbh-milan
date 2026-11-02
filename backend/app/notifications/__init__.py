"""
Notifications to families and owners.

Templates carry only the case number and the station name: never names, descriptions, locations of a
found person, or contact details. Delivery goes through a provider interface; no real SMS or WhatsApp
provider is integrated (that needs provider credentials and, in India, DLT-registered sender IDs and
templates).
"""

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

from app.config import get_settings
from app.models.enums import NotificationChannel, NotificationTemplate

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
}
LOCALES = ("en", "hi")
_PHONE = re.compile(r"^\+[1-9]\d{7,14}$")


def normalise_phone(raw: str, default_country_code: str) -> str | None:
    digits = re.sub(r"[\s\-().]", "", raw or "")
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if not digits.startswith("+"):
        digits = default_country_code + digits.lstrip("0")
    return digits if _PHONE.match(digits) else None


def mask(phone: str) -> str:
    return "•" * 4 + phone[-4:]


def render(template: NotificationTemplate, locale: str, *, case_number: str, station: str) -> str:
    return TEMPLATES[template][locale].format(case_number=case_number, station=station)


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

    def send(self, *, channel: NotificationChannel, to: str, body: str, idempotency_key: str) -> Delivery: ...


class DisabledProvider:
    """No provider configured: messages stay PENDING; nothing is sent and no attempts are consumed."""

    name = "disabled"
    enabled = False

    def send(self, **_) -> Delivery:  # pragma: no cover - the worker never calls a disabled provider
        raise ProviderError("notification provider disabled", permanent=False)


class MockProvider:
    """Deterministic local provider. Records deliveries and can simulate a temporary or permanent failure."""

    name = "mock"
    enabled = True

    def __init__(self) -> None:
        self.deliveries: list[dict[str, str]] = []
        self.fail: ProviderError | None = None

    def send(self, *, channel: NotificationChannel, to: str, body: str, idempotency_key: str) -> Delivery:
        if self.fail is not None:
            raise self.fail
        self.deliveries.append({"channel": str(channel), "to": mask(to), "body": body, "key": idempotency_key})
        return Delivery(provider_message_id=f"mock-{idempotency_key[:40]}")


class ConsoleProvider:
    """Local development only: logs a masked recipient and the rendered message."""

    name = "console"
    enabled = True

    def send(self, *, channel: NotificationChannel, to: str, body: str, idempotency_key: str) -> Delivery:
        logger.info("Console notification", extra={"channel": channel, "to": mask(to), "body": body})
        return Delivery(provider_message_id=f"console-{idempotency_key[:40]}")


@lru_cache
def get_provider() -> NotificationProvider:
    choice = get_settings().notification_provider
    if choice == "console":
        return ConsoleProvider()
    if choice == "mock":
        return MockProvider()
    return DisabledProvider()
