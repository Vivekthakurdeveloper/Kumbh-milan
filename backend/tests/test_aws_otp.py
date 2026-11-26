"""OTP and password-reset delivery through Amazon SNS (SMS) and Amazon SES (email).

Nothing here reaches real AWS: moto intercepts SNS and SES, and the SNS unit tests use a recording client.
"""

import logging
import re
from contextlib import contextmanager

import boto3
import pytest
from botocore.exceptions import BotoCoreError, ClientError

from app.config import get_settings
from app.config.settings import Settings
from app.models.enums import NotificationChannel, NotificationTemplate
from app.notifications import AwsSnsProvider, ProviderError, reset_provider_cache
from app.notifications.email import EmailError, OutgoingEmail, SesEmailSender, get_email_sender
from tests.helpers import API
from tests.test_phase4_public import _otp_request
from tests.test_platform import PRODUCTION_OK

REGION = "ap-south-1"
ENTITY_ID = "1101000000000000001"
SENDER_ID = "KUMBHS"
TEMPLATE_IDS = {"OTP_CODE:en": "2001", "OTP_CODE:hi": "2002", "PASSWORD_RESET": "2003"}


@contextmanager
def _settings(**values):
    """Overrides settings for one block and clears the provider caches on both sides."""
    settings = get_settings()
    saved = {key: getattr(settings, key) for key in values}
    for key, value in values.items():
        setattr(settings, key, value)
    reset_provider_cache()
    get_email_sender.cache_clear()
    try:
        yield settings
    finally:
        for key, value in saved.items():
            setattr(settings, key, value)
        reset_provider_cache()
        get_email_sender.cache_clear()


SNS_ON = dict(
    notification_provider="aws_sns", aws_region=REGION, aws_sns_entity_id=ENTITY_ID,
    aws_sns_sender_id=SENDER_ID, aws_sns_template_ids=TEMPLATE_IDS,
)


class RecordingSns:
    def __init__(self, response=None, error=None):
        self.calls: list[dict] = []
        self.response = response or {"MessageId": "msg-1"}
        self.error = error

    def publish(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def _provider_with(client) -> AwsSnsProvider:
    provider = AwsSnsProvider()
    provider._client = client
    return provider


def _sms_sent() -> list[tuple[str, str]]:
    from moto.sns.models import sns_backends

    account = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
    return list(sns_backends[account][REGION].sms_messages.values())


def test_sns_publish_carries_dlt_attributes_and_the_locale_template():
    client = RecordingSns()
    with _settings(**SNS_ON):
        provider = _provider_with(client)
        delivery = provider.send(
            channel=NotificationChannel.SMS, to="+919876543210", body="कुंभ सेतु सत्यापन कोड: 123456।",
            idempotency_key="challenge-1", template=NotificationTemplate.OTP_CODE, locale="hi",
            extras={"otp": "123456"},
        )
    call = client.calls[0]
    attributes = {key: value["StringValue"] for key, value in call["MessageAttributes"].items()}
    assert call["PhoneNumber"] == "+919876543210"
    assert attributes == {
        "AWS.SNS.SMS.SMSType": "Transactional",
        "AWS.SNS.SMS.SenderID": SENDER_ID,
        "AWS.MM.SMS.EntityId": ENTITY_ID,
        "AWS.MM.SMS.TemplateId": "2002",
    }
    assert delivery.provider_message_id == "sns-msg-1"


def test_sns_template_falls_back_to_the_plain_key_and_refuses_unregistered_messages():
    client = RecordingSns()
    with _settings(**SNS_ON):
        provider = _provider_with(client)
        provider.send(channel=NotificationChannel.SMS, to="+919876543210", body="x", idempotency_key="k",
                      template=NotificationTemplate.PASSWORD_RESET, locale="en")
        assert client.calls[0]["MessageAttributes"]["AWS.MM.SMS.TemplateId"]["StringValue"] == "2003"
        with pytest.raises(ProviderError) as missing:
            provider.send(channel=NotificationChannel.SMS, to="+919876543210", body="x", idempotency_key="k",
                          template=NotificationTemplate.CASE_CLOSED, locale="en")
    assert missing.value.permanent is True
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    ("error", "permanent"),
    [
        (ClientError({"Error": {"Code": "OptedOut", "Message": "opted out"}}, "Publish"), True),
        (ClientError({"Error": {"Code": "InvalidParameter", "Message": "bad"}}, "Publish"), True),
        (ClientError({"Error": {"Code": "Throttling", "Message": "slow down"}}, "Publish"), False),
        (BotoCoreError(), False),
    ],
)
def test_sns_errors_are_classified_for_the_retry_policy(error, permanent):
    with _settings(**SNS_ON):
        provider = _provider_with(RecordingSns(error=error))
        with pytest.raises(ProviderError) as raised:
            provider.send(channel=NotificationChannel.SMS, to="+919876543210", body="x", idempotency_key="k",
                          template=NotificationTemplate.OTP_CODE, locale="en")
    assert raised.value.permanent is permanent
    assert "123" not in str(raised.value)


def test_sns_without_dlt_ids_fails_each_send_and_transmits_nothing():
    client = RecordingSns()
    with _settings(**{**SNS_ON, "aws_sns_entity_id": None}):
        provider = _provider_with(client)
        with pytest.raises(ProviderError) as raised:
            provider.send(channel=NotificationChannel.SMS, to="+919876543210", body="x", idempotency_key="k",
                          template=NotificationTemplate.OTP_CODE, locale="en")
    assert raised.value.permanent is True
    assert client.calls == []


def test_missing_dlt_ids_do_not_fail_the_otp_request(client, caplog):
    with _settings(**{**SNS_ON, "aws_sns_entity_id": None}), caplog.at_level(logging.ERROR, logger="kumbh_setu.otp"):
        response = _otp_request(client, mobile="9876543212", purpose="REPORT_MISSING")
    assert response.status_code == 200, response.text
    assert [phone for phone, _ in _sms_sent() if phone == "+919876543212"] == []
    assert any("Citizen OTP could not be sent" in record.getMessage() for record in caplog.records)


def test_citizen_otp_is_sent_by_sns_and_the_code_verifies(client):
    with _settings(**SNS_ON):
        response = _otp_request(client, mobile="9876543210", purpose="REPORT_MISSING")
        assert response.status_code == 200, response.text
        sent = [body for phone, body in _sms_sent() if phone == "+919876543210"]
        assert len(sent) == 1
        code = re.search(r"(\d{6})", sent[0]).group(1)
        verified = client.post(f"{API}/public/otp/verify",
                               json={"mobile_number": "9876543210", "purpose": "REPORT_MISSING", "code": code})
        assert verified.status_code == 200, verified.text
        assert verified.json()["verified_mobile_token"]


def test_a_failed_otp_send_is_logged_and_the_response_is_unchanged(client, caplog):
    misconfigured = {**SNS_ON, "aws_sns_template_ids": {}}
    with _settings(**misconfigured), caplog.at_level(logging.ERROR, logger="kumbh_setu.otp"):
        response = _otp_request(client, mobile="9876543211", purpose="REPORT_MISSING")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "OTP_SENT"
    assert [phone for phone, _ in _sms_sent() if phone == "+919876543211"] == []
    assert any("Citizen OTP could not be sent" in record.getMessage() for record in caplog.records)
    assert "9876543211" not in caplog.text


def test_email_provider_setting_selects_ses(storage):
    """The password reset path calls get_email_sender(), so this selects SES for reset codes too."""
    with _settings(email_provider="aws_ses"):
        assert isinstance(get_email_sender(), SesEmailSender)


def test_password_reset_email_is_sent_through_ses(storage):
    from moto.ses.models import ses_backends

    account = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
    backend = ses_backends[account][REGION]
    boto3.client("ses", region_name=REGION).verify_email_identity(EmailAddress="no-reply@kumbh.example")
    before = len(backend.sent_messages)
    with _settings(email_provider="aws_ses", email_from="Kumbh Setu <no-reply@kumbh.example>",
                   aws_region=REGION):
        get_email_sender().send(
            OutgoingEmail("citizen@example.com", "Kumbh Setu password reset code", "Your code is 654321")
        )
    sent = backend.sent_messages[before:]
    assert len(sent) == 1
    assert sent[0].subject == "Kumbh Setu password reset code"
    assert "654321" in sent[0].body


def test_ses_refuses_an_unverified_sender_as_an_email_error(storage):
    with _settings(email_provider="aws_ses", email_from="Kumbh Setu <unverified@kumbh.example>",
                   aws_region=REGION):
        with pytest.raises(EmailError):
            SesEmailSender(get_settings()).send(OutgoingEmail("citizen@example.com", "s", "b"))


def test_production_refuses_sns_without_dlt_ids_and_ses_without_a_real_sender():
    with pytest.raises(ValueError, match="AWS_SNS_ENTITY_ID"):
        Settings(**{**PRODUCTION_OK, "notification_provider": "aws_sns"})
    with pytest.raises(ValueError, match="EMAIL_FROM"):
        Settings(**{**PRODUCTION_OK, "email_provider": "aws_ses"})
    ok = Settings(**{**PRODUCTION_OK, "notification_provider": "aws_sns", "aws_sns_entity_id": ENTITY_ID,
                     "aws_sns_sender_id": SENDER_ID, "email_provider": "aws_ses",
                     "email_from": "Kumbh Setu <no-reply@kumbh.example>"})
    assert ok.notification_provider == "aws_sns" and ok.email_provider == "aws_ses"
