from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDER_MARKERS = ("change-me", "changeme", "placeholder", "example")
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})
_INTERNAL_HOST_MARKERS = ("objectstore", ".internal", ".local", "host.docker.internal")


def _is_browser_unreachable_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return (
        host in _LOOPBACK_HOSTS
        or host.endswith(".localhost")
        or any(marker in host for marker in _INTERNAL_HOST_MARKERS)
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    app_env: Literal["local", "test", "staging", "production"] = "local"
    app_name: str = "Kumbh Setu Lost & Found API"
    docs_enabled: bool = True
    log_level: str = "INFO"
    cors_origins: list[str] = Field(default_factory=list)

    database_url: str = "postgresql+psycopg://kumbh:kumbh@localhost:5432/kumbh_setu"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout_seconds: int = 10
    db_statement_timeout_ms: int = 15000
    kumbh_app_db_role: str | None = None

    jwt_secret: str = "change-me-local-only-jwt-secret-0000000000"
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    jwt_issuer: str = "kumbh-setu"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 7
    password_min_length: int = 12
    login_max_failures: int = 5
    login_lockout_minutes: int = 15

    contact_encryption_key: str = ""
    contact_encryption_previous_keys: list[str] = Field(
        default_factory=list,
        description="JSON list of retired Fernet keys still accepted for decryption until rotate-encryption finishes",
    )
    public_security_secret: str = Field(
        "",
        description="HMAC/JWT secret for citizen OTP and case-scoped tokens; falls back to JWT_SECRET in local/test only",
    )

    case_number_prefix: str = "KSLF"
    idempotency_ttl_hours: int = 24
    sync_max_batch: int = Field(50, ge=1, le=100, description="Maximum records accepted in one sync push")
    sync_pull_lag_seconds: int = Field(
        5,
        ge=0,
        description="Pull omits changes newer than this lag so a late-committing transaction is never skipped",
    )
    sync_attachment_session_ttl_hours: int = Field(72, ge=1, le=720)
    sync_attachment_max_open_sessions_per_device: int = Field(20, ge=1, le=100)

    public_intake_user_id: UUID | None = Field(
        None, description="Active HELP_DESK_OPERATOR used as reported_by for verified public submissions"
    )
    public_intake_station_id: UUID | None = Field(
        None, description="Station that receives public submissions and unlinked sightings"
    )
    otp_ttl_seconds: int = Field(300, ge=60, le=1800)
    otp_max_attempts: int = Field(5, ge=1, le=10)
    otp_resend_seconds: int = Field(60, ge=15, le=600)
    otp_length: int = Field(6, ge=4, le=8)
    public_status_token_ttl_minutes: int = Field(15, ge=1, le=60)
    public_verified_mobile_token_ttl_minutes: int = Field(30, ge=1, le=120)

    s3_endpoint_url: str | None = None
    s3_public_endpoint_url: str | None = None
    s3_region: str = "ap-south-1"
    s3_bucket: str = "kumbh-setu-media"
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_server_side_encryption: str = ""
    s3_presign_ttl_seconds: int = 300
    media_delivery: Literal["auto", "presigned", "proxy"] = Field(
        "auto",
        description=(
            "How browsers fetch private photos and recordings. presigned: direct links to the object store (needs a "
            "host the browser can reach). proxy: short-lived links served by this API. auto: presigned when "
            "S3_PUBLIC_ENDPOINT_URL is set or the store is AWS S3 (no S3_ENDPOINT_URL), otherwise proxy."
        ),
    )
    api_public_base_url: str | None = Field(
        None,
        description=(
            "Public origin of this API (https://api.example.org), used to build proxy media links. "
            "Falls back to the origin of the incoming request, which is wrong behind a reverse proxy."
        ),
    )

    max_photo_bytes: int = 10 * 1024 * 1024
    max_audio_bytes: int = 25 * 1024 * 1024
    max_request_bytes: int = 30 * 1024 * 1024

    matching_auto_enabled: bool = Field(True, description="Suggest matches after a case is registered or edited")
    handover_required_for_resolve: bool = Field(
        False,
        description=(
            "Require a completed structured handover before resolving person cases. "
            "Keep false until staff clients support the handover workflow."
        ),
    )

    face_matching_enabled: bool = Field(True, description="Analyse person photos and suggest face-based candidates")
    face_engine: Literal["opencv_sface"] = "opencv_sface"
    face_model_dir: str = Field("models/faces", description="Directory holding YuNet and SFace ONNX files")
    face_similarity_threshold: float = Field(
        0.363, description="SFace cosine similarity operating point; pairs below this are not stored"
    )
    face_max_candidates: int = 5
    face_candidate_window_days: int = 14
    face_detection_min_score: float = 0.9
    face_min_size_px: int = 40
    face_embedding_encryption_key: str = Field(
        "", description="Fernet key for facial embeddings; separate from contact encryption"
    )
    face_embedding_encryption_previous_keys: list[str] = Field(
        default_factory=list, description="Retired face-embedding keys accepted for decryption during a rotation"
    )
    face_embedding_retention_days: int = Field(
        30, description="Operational default: delete embeddings for inactive cases after this many days"
    )

    retention_archive_after_days: int = 90
    retention_anonymize_after_days: int = 365
    retention_attachment_purge_after_days: int = 30
    retention_grievance_anonymize_after_days: int = Field(
        365, description="Code default, not a legally approved period: anonymise grievances this long after closure"
    )
    retention_max_items_per_run: int = 5000
    retention_database_url: str | None = Field(
        None, description="Owner connection for executing anonymisation runs (retention task only, never the API)"
    )

    notification_provider: Literal["disabled", "console", "mock", "msg91", "aws_sns"] = Field(
        "disabled",
        description=(
            "SMS provider. console/mock are local/test only; msg91 and aws_sns need credentials and DLT templates. "
            "aws_sns uses Amazon SNS (pay per message, no monthly fee)."
        ),
    )
    aws_region: str = Field("ap-south-1", description="Region for SNS and SES. ap-south-1 keeps traffic in India.")
    aws_timeout_seconds: float = 10.0
    aws_sns_sender_id: str | None = Field(
        None, description="Alphabetic sender ID registered with the SNS region and DLT (3-11 characters)"
    )
    aws_sns_entity_id: str | None = Field(
        None, description="TRAI principal entity (PE) id from DLT; required by carriers for Indian numbers"
    )
    aws_sns_sms_type: Literal["Transactional", "Promotional"] = "Transactional"
    aws_sns_template_ids: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "DLT template id per message. Keys are NotificationTemplate values, optionally suffixed with the locale "
            "(for example OTP_CODE:hi). The message text must match the registered DLT template exactly."
        ),
    )
    notification_max_attempts: int = 5
    notification_retry_base_seconds: int = 60
    notification_stale_after_seconds: int = 300
    notification_default_country_code: str = "+91"
    msg91_auth_key: str | None = None
    msg91_sender_id: str | None = None
    msg91_base_url: str = "https://control.msg91.com/api/v5"
    msg91_template_ids: dict[str, str] = Field(
        default_factory=dict,
        description="Map of NotificationTemplate value -> MSG91 DLT template id",
    )
    msg91_timeout_seconds: float = 10.0
    msg91_webhook_secret: str | None = Field(
        None, description="Shared secret MSG91 sends in X-Webhook-Token with delivery receipts; never log this value"
    )

    assignment_requires_approval: bool = Field(
        True, description="Police assignments become requests that an ADMIN supervisor approves"
    )
    grievance_sla_hours: int = Field(72, ge=1, le=720)
    grievance_max_escalation_level: int = Field(2, ge=1, le=5)
    grievance_max_attachments: int = Field(5, ge=1, le=20)
    report_max_rows: int = Field(50_000, ge=100, le=500_000)
    report_max_period_days: int = Field(92, ge=1, le=366)
    report_artifact_ttl_hours: int = Field(72, ge=1, le=720)
    report_download_ttl_seconds: int = Field(300, ge=30, le=3600)
    integration_retry_base_seconds: int = 30
    integration_circuit_failures: int = 5
    integration_circuit_open_seconds: int = 300
    integration_signature_tolerance_seconds: int = 300
    integration_allowed_hosts: list[str] = Field(
        default_factory=list,
        description="Hostnames outbound webhooks may call. Empty allows any host only in local/test.",
    )
    realtime_poll_interval_seconds: float = Field(2.0, ge=0.1, le=30)
    realtime_stream_max_seconds: int = Field(25, ge=1, le=300)
    realtime_lag_seconds: float = Field(
        2.0, ge=0, le=60, description="Events newer than this are held back so out-of-order commits are not skipped"
    )
    chat_llm_provider: Literal["disabled", "mock", "openai_compatible"] = Field(
        "disabled",
        description="openai_compatible targets a self-hosted endpoint (vLLM, Ollama, llama.cpp) inside the deployment",
    )
    chat_llm_base_url: str | None = None
    chat_llm_model: str | None = None
    chat_llm_api_key: str | None = None
    chat_llm_timeout_seconds: float = 15.0
    chat_session_ttl_minutes: int = 60
    chat_max_messages_per_session: int = 40
    chat_supported_locales: list[str] = Field(
        default_factory=lambda: ["en", "hi", "bn", "mr", "gu", "ta", "te", "kn", "ml", "pa", "or", "ur"],
        description="Locales the chatbot accepts. Deterministic replies exist for en/hi; others need the LLM.",
    )
    whatsapp_webhook_secret: str | None = Field(
        None, description="Shared secret the WhatsApp gateway sends in X-Webhook-Token; never log this value"
    )

    smtp_enabled: bool = False
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True
    smtp_from_email: str | None = None
    smtp_from_name: str = "Kumbh Setu"
    smtp_security: Literal["starttls", "ssl", "none"] = Field(
        "starttls", description="none is only for a local relay such as MailHog"
    )
    smtp_timeout_seconds: float = 10.0

    fcm_enabled: bool = False
    fcm_credentials_json: str | None = Field(
        None, description="Firebase service-account JSON string; never log this value"
    )
    fcm_credentials_file: str | None = Field(
        None, description="Optional path to a Firebase service-account JSON file"
    )

    geo_operating_area: list[float] | None = Field(
        default_factory=lambda: [29.70, 77.90, 30.25, 78.45],
        description="[min_lat, min_lng, max_lat, max_lng]; case coordinates outside are rejected (catches swapped values)",
    )
    geo_max_radius_m: int = 10_000
    geo_max_bbox_degrees: float = 0.5
    geo_map_limit: int = 500

    scan_mode: Literal["clamav", "disabled"] | None = Field(
        None, description="clamav (required in staging/production) or disabled (development). Default: clamav if CLAMAV_HOST is set."
    )
    clamav_host: str | None = None
    clamav_port: int = 3310
    clamav_connect_timeout_seconds: float = 3.0
    clamav_timeout_seconds: float = 60.0
    clamav_max_scan_bytes: int = 30 * 1024 * 1024
    scan_max_attempts: int = 6
    scan_retry_base_seconds: int = 30
    scan_stale_after_seconds: int = 300

    log_format: Literal["json", "text"] = "json"
    metrics_token: str | None = None
    trusted_proxies: list[str] = Field(
        default_factory=list,
        description="CIDRs of load balancers/proxies whose X-Forwarded-For is trusted. Empty: use the socket peer.",
    )

    redis_url: str | None = None
    redis_timeout_ms: int = 250

    rate_limit_enabled: bool = True
    rate_limit_backend: Literal["memory", "redis"] = "memory"
    rate_limit_per_minute: int = 600
    rate_limit_login_per_minute: int = 30
    rate_limit_refresh_per_minute: int = 60
    rate_limit_password_per_hour: int = 10
    rate_limit_register_per_minute: int = 10
    rate_limit_forgot_password_per_hour: int = 10
    rate_limit_reset_password_per_hour: int = 20
    rate_limit_upload_per_minute: int = 30
    rate_limit_case_create_per_minute: int = 60
    rate_limit_search_per_minute: int = 180
    rate_limit_public_otp_per_minute: int = 10
    rate_limit_public_submit_per_minute: int = 20
    rate_limit_public_status_per_minute: int = 30
    rate_limit_public_chat_per_minute: int = 30
    rate_limit_mfa_verify_per_minute: int = 30
    rate_limit_assistant_per_minute: int = 20
    workers_required: bool = Field(
        False,
        description="When true, readiness fails unless scan, notification, cleanup, and retention workers have fresh heartbeats.",
    )
    worker_heartbeat_max_age_seconds: int = 180
    password_reset_code_length: int = Field(8, ge=6, le=12, description="Digits in the emailed or texted reset code")
    password_reset_ttl_minutes: int = Field(15, ge=5, le=60)
    password_reset_max_attempts: int = Field(5, ge=1, le=10, description="Wrong codes before a reset code is burned")
    password_reset_max_requests_per_hour: int = Field(
        3, ge=1, le=20, description="Codes sent per account per hour; further requests are accepted but not sent"
    )
    email_provider: Literal["disabled", "console", "mock", "smtp", "aws_ses"] = Field(
        "disabled",
        description=(
            "smtp and aws_ses send real email (aws_ses uses Amazon SES, $0.10 per 1,000 messages). "
            "console or mock are local only. disabled turns email delivery off."
        ),
    )
    email_from: str = Field("Kumbh Setu <no-reply@localhost>", description="From header for outgoing email")
    mfa_mode: Literal["disabled", "totp"] = Field(
        "disabled",
        description="totp is an optional step-up factor. It is not phishing-resistant and is not a production approval.",
    )
    mfa_login_challenge_ttl_seconds: int = Field(
        300, ge=60, le=900, description="How long the challenge from an MFA-required login stays valid"
    )
    mfa_login_max_attempts: int = Field(
        5, ge=1, le=10, description="Wrong authenticator codes before a login challenge is burned"
    )

    assistant_provider: Literal["disabled", "faq", "llm"] = Field(
        "faq",
        description=(
            "Staff help assistant. faq answers from the built-in knowledge base. llm phrases answers with an "
            "OpenAI-compatible model grounded only on that knowledge base. disabled returns 503."
        ),
    )
    assistant_llm_base_url: str | None = Field(None, description="Falls back to CHAT_LLM_BASE_URL")
    assistant_llm_model: str | None = Field(None, description="Falls back to CHAT_LLM_MODEL")
    assistant_llm_api_key: str | None = Field(None, description="Falls back to CHAT_LLM_API_KEY; never log this value")
    assistant_llm_timeout_seconds: float = Field(15.0, gt=0, le=60)
    assistant_llm_fallback_to_faq: bool = Field(
        True, description="Answer from the knowledge base when the model fails, instead of returning 503"
    )

    @property
    def media_mode(self) -> Literal["presigned", "proxy"]:
        if self.media_delivery != "auto":
            return self.media_delivery
        if self.s3_public_endpoint_url or not self.s3_endpoint_url:
            return "presigned"
        return "proxy"

    def _validate_keyrings(self) -> None:
        from cryptography.fernet import Fernet

        for name, primary, previous in (
            ("CONTACT_ENCRYPTION", self.contact_encryption_key, self.contact_encryption_previous_keys),
            ("FACE_EMBEDDING_ENCRYPTION", self.face_embedding_encryption_key,
             self.face_embedding_encryption_previous_keys),
        ):
            if previous and not primary:
                raise ValueError(f"{name}_PREVIOUS_KEYS requires {name}_KEY")
            if primary in previous:
                raise ValueError(f"{name}_PREVIOUS_KEYS must not contain the current key")
            for key in previous:
                try:
                    Fernet(key.encode())
                except ValueError as exc:
                    raise ValueError(f"{name}_PREVIOUS_KEYS contains an invalid Fernet key") from exc

    @model_validator(mode="after")
    def _validate_production_secrets(self) -> "Settings":
        if self.email_provider == "smtp" and not self.smtp_host:
            raise ValueError("EMAIL_PROVIDER=smtp requires SMTP_HOST")
        if self.scan_mode is None:
            self.scan_mode = "clamav" if self.clamav_host else "disabled"
        if self.scan_mode == "clamav" and not self.clamav_host:
            raise ValueError("SCAN_MODE=clamav requires CLAMAV_HOST")
        self._validate_keyrings()
        if self.app_env in ("staging", "production"):
            if self.notification_provider in ("console", "mock"):
                raise ValueError("NOTIFICATION_PROVIDER=console/mock is local/test only")
            if self.notification_provider == "msg91" and not self.msg91_auth_key:
                raise ValueError("NOTIFICATION_PROVIDER=msg91 requires MSG91_AUTH_KEY")
            if self.notification_provider == "msg91" and not self.msg91_webhook_secret:
                raise ValueError("NOTIFICATION_PROVIDER=msg91 requires MSG91_WEBHOOK_SECRET for delivery receipts")
            if self.notification_provider == "aws_sns" and not (self.aws_sns_entity_id and self.aws_sns_sender_id):
                raise ValueError("NOTIFICATION_PROVIDER=aws_sns requires AWS_SNS_ENTITY_ID and AWS_SNS_SENDER_ID")
            if self.email_provider == "aws_ses" and "@localhost" in self.email_from:
                raise ValueError("EMAIL_FROM must be a verified SES sender outside local and test")
            if self.chat_llm_provider == "mock":
                raise ValueError("CHAT_LLM_PROVIDER=mock is local/test only")
            if self.chat_llm_provider == "openai_compatible" and not (self.chat_llm_base_url and self.chat_llm_model):
                raise ValueError("CHAT_LLM_PROVIDER=openai_compatible requires CHAT_LLM_BASE_URL and CHAT_LLM_MODEL")
            if self.assistant_provider == "llm" and not (
                (self.assistant_llm_base_url or self.chat_llm_base_url)
                and (self.assistant_llm_model or self.chat_llm_model)
            ):
                raise ValueError("ASSISTANT_PROVIDER=llm requires ASSISTANT_LLM_BASE_URL and ASSISTANT_LLM_MODEL")
            if self.smtp_enabled and (not self.smtp_host or not self.smtp_from_email):
                raise ValueError("SMTP_ENABLED requires SMTP_HOST and SMTP_FROM_EMAIL")
            if self.fcm_enabled and not (self.fcm_credentials_json or self.fcm_credentials_file):
                raise ValueError("FCM_ENABLED requires FCM_CREDENTIALS_JSON or FCM_CREDENTIALS_FILE")
            if not self.workers_required:
                raise ValueError("WORKERS_REQUIRED must be true so readiness checks supervised workers")
            if self.scan_mode != "clamav":
                raise ValueError("SCAN_MODE must be clamav outside local and test; unscanned files are never approved")
            if len(self.jwt_secret) < 32 or any(m in self.jwt_secret.lower() for m in PLACEHOLDER_MARKERS):
                raise ValueError("JWT_SECRET must be a strong, non-placeholder secret of at least 32 characters")
            if not self.contact_encryption_key:
                raise ValueError("CONTACT_ENCRYPTION_KEY is required outside local and test environments")
            if not self.public_security_secret or len(self.public_security_secret) < 32:
                raise ValueError("PUBLIC_SECURITY_SECRET (32+ characters) is required outside local and test")
            if self.public_intake_user_id is None or self.public_intake_station_id is None:
                raise ValueError("PUBLIC_INTAKE_USER_ID and PUBLIC_INTAKE_STATION_ID are required outside local/test")
            if self.face_matching_enabled and not self.face_embedding_encryption_key:
                raise ValueError(
                    "FACE_EMBEDDING_ENCRYPTION_KEY is required outside local and test when face matching is enabled"
                )
            if self.docs_enabled and self.app_env == "production":
                raise ValueError("DOCS_ENABLED must be false in production")
            if self.rate_limit_backend != "redis" or not self.redis_url:
                raise ValueError("RATE_LIMIT_BACKEND=redis and REDIS_URL are required so limits hold across replicas")
            if not self.metrics_token or len(self.metrics_token) < 24:
                raise ValueError("METRICS_TOKEN (24+ characters) is required to protect /metrics")
            if "*" in self.cors_origins:
                raise ValueError("CORS_ORIGINS must list explicit origins outside local development")
            if self.email_provider in ("console", "mock"):
                raise ValueError("EMAIL_PROVIDER must be smtp or disabled outside local and test")
            if self.media_mode == "presigned":
                if self.s3_endpoint_url and not self.s3_public_endpoint_url:
                    raise ValueError("MEDIA_DELIVERY=presigned with S3_ENDPOINT_URL requires S3_PUBLIC_ENDPOINT_URL")
                if self.s3_public_endpoint_url:
                    self._require_browser_reachable_url(
                        self.s3_public_endpoint_url, setting="S3_PUBLIC_ENDPOINT_URL"
                    )
            if self.media_mode == "proxy" and not self.api_public_base_url:
                raise ValueError(
                    "API_PUBLIC_BASE_URL is required when media links are served by the API "
                    "(MEDIA_DELIVERY=proxy, or S3_ENDPOINT_URL set without S3_PUBLIC_ENDPOINT_URL)"
                )
            if self.api_public_base_url:
                self._require_browser_reachable_url(self.api_public_base_url, setting="API_PUBLIC_BASE_URL")
        return self

    def _require_browser_reachable_url(self, value: str, *, setting: str) -> None:
        """Validate a browser-facing media URL outside local and test environments."""
        parsed = urlparse(value.strip())
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError(f"{setting} must be an absolute http(s) URL")
        if _is_browser_unreachable_url(value):
            raise ValueError(
                f"{setting} must be a browser-reachable hostname "
                "(not localhost, loopback, or docker-internal names)"
            )
        if self.app_env == "production" and parsed.scheme != "https":
            raise ValueError(f"{setting} must use https in production")


@lru_cache
def get_settings() -> Settings:
    return Settings()
