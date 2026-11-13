"""
Outgoing email for account security messages (password reset codes).

smtp is the only real transport (standard library, no vendor SDK). console and mock are local only: console
logs the whole message, including the code, so settings validation refuses it in staging and production.
"""

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from functools import lru_cache
from typing import Protocol

from app.config import Settings, get_settings

logger = logging.getLogger("kumbh_setu.email")


class EmailError(Exception):
    pass


@dataclass(frozen=True)
class OutgoingEmail:
    to: str
    subject: str
    body: str


class EmailSender(Protocol):
    name: str
    enabled: bool

    def send(self, message: OutgoingEmail) -> None: ...


class DisabledEmailSender:
    name = "disabled"
    enabled = False

    def send(self, message: OutgoingEmail) -> None:  # pragma: no cover - callers check .enabled
        raise EmailError("email delivery is disabled")


class ConsoleEmailSender:
    name = "console"
    enabled = True

    def send(self, message: OutgoingEmail) -> None:
        logger.info("Console email to %s | %s\n%s", message.to, message.subject, message.body)


class MockEmailSender:
    name = "mock"
    enabled = True

    def __init__(self) -> None:
        self.sent: list[OutgoingEmail] = []
        self.fail: Exception | None = None

    def send(self, message: OutgoingEmail) -> None:
        if self.fail is not None:
            raise self.fail
        self.sent.append(message)


class SmtpEmailSender:
    name = "smtp"
    enabled = True

    def __init__(self, settings: Settings) -> None:
        self.s = settings

    def send(self, message: OutgoingEmail) -> None:
        mail = EmailMessage()
        mail["From"] = self.s.email_from
        mail["To"] = message.to
        mail["Subject"] = message.subject
        mail.set_content(message.body)
        try:
            if self.s.smtp_security == "ssl":
                client: smtplib.SMTP = smtplib.SMTP_SSL(
                    self.s.smtp_host, self.s.smtp_port, timeout=self.s.smtp_timeout_seconds,
                    context=ssl.create_default_context(),
                )
            else:
                client = smtplib.SMTP(self.s.smtp_host, self.s.smtp_port, timeout=self.s.smtp_timeout_seconds)
            with client:
                if self.s.smtp_security == "starttls":
                    client.starttls(context=ssl.create_default_context())
                if self.s.smtp_username:
                    client.login(self.s.smtp_username, self.s.smtp_password or "")
                client.send_message(mail)
        except (smtplib.SMTPException, OSError) as exc:
            raise EmailError(type(exc).__name__) from exc


@lru_cache
def get_email_sender() -> EmailSender:
    settings = get_settings()
    if settings.email_provider == "smtp":
        return SmtpEmailSender(settings)
    if settings.email_provider == "console":
        return ConsoleEmailSender()
    if settings.email_provider == "mock":
        return MockEmailSender()
    return DisabledEmailSender()
