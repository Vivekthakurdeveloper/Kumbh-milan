from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDER_MARKERS = ("change-me", "changeme", "placeholder", "example")
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_INTERNAL_HOST_MARKERS = ("objectstore", ".internal", ".local", "host.docker.internal")


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

    max_photo_bytes: int = 10 * 1024 * 1024
    max_audio_bytes: int = 25 * 1024 * 1024
    max_request_bytes: int = 30 * 1024 * 1024

    matching_auto_enabled: bool = Field(True, description="Suggest matches after a case is registered or edited")

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
    face_embedding_retention_days: int = Field(
        30, description="Operational default: delete embeddings for inactive cases after this many days"
    )

    retention_archive_after_days: int = 90
    retention_anonymize_after_days: int = 365
    retention_attachment_purge_after_days: int = 30
    retention_max_items_per_run: int = 5000
    retention_database_url: str | None = Field(
        None, description="Owner connection for executing anonymisation runs (retention task only, never the API)"
    )

    notification_provider: Literal["disabled", "console", "mock", "msg91"] = Field(
        "disabled",
        description="SMS/WhatsApp provider. console/mock are local/test only; msg91 requires credentials and DLT templates.",
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

    smtp_enabled: bool = False
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True
    smtp_from_email: str | None = None
    smtp_from_name: str = "Kumbh Setu"
    smtp_timeout_seconds: float = 15.0

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
    password_reset_token_ttl_minutes: int = Field(30, ge=5, le=120)
    workers_required: bool = Field(
        False,
        description="When true, readiness fails unless scan, notification, cleanup, and retention workers have fresh heartbeats.",
    )
    worker_heartbeat_max_age_seconds: int = 180
    mfa_mode: Literal["disabled", "totp"] = Field(
        "disabled",
        description="totp is an optional step-up factor. It is not phishing-resistant and is not a production approval.",
    )

    @model_validator(mode="after")
    def _validate_production_secrets(self) -> "Settings":
        if self.scan_mode is None:
            self.scan_mode = "clamav" if self.clamav_host else "disabled"
        if self.scan_mode == "clamav" and not self.clamav_host:
            raise ValueError("SCAN_MODE=clamav requires CLAMAV_HOST")
        if self.app_env in ("staging", "production"):
            if self.notification_provider in ("console", "mock"):
                raise ValueError("NOTIFICATION_PROVIDER=console/mock is local/test only")
            if self.notification_provider == "msg91" and not self.msg91_auth_key:
                raise ValueError("NOTIFICATION_PROVIDER=msg91 requires MSG91_AUTH_KEY")
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
            self._require_browser_reachable_s3_public_endpoint()
        return self

    def _require_browser_reachable_s3_public_endpoint(self) -> None:
        """
        Presigned photo URLs embed S3_PUBLIC_ENDPOINT_URL (or fall back to S3_ENDPOINT_URL).
        Outside local/test the signed host must be an explicit HTTP(S) URL that browsers can reach —
        never a loopback or docker-internal hostname.
        """
        public = (self.s3_public_endpoint_url or "").strip()
        if not public:
            # Empty public endpoint is only valid when using real AWS (no custom endpoint_url).
            if self.s3_endpoint_url:
                raise ValueError(
                    "S3_PUBLIC_ENDPOINT_URL is required when S3_ENDPOINT_URL is set so browsers "
                    "never receive internal/localhost object-store addresses"
                )
            return
        parsed = urlparse(public)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("S3_PUBLIC_ENDPOINT_URL must be an absolute http(s) URL")
        host = parsed.hostname.lower()
        if host in _LOOPBACK_HOSTS or any(marker in host for marker in _INTERNAL_HOST_MARKERS):
            raise ValueError(
                "S3_PUBLIC_ENDPOINT_URL must be a browser-reachable hostname "
                "(not localhost, loopback, or docker-internal names)"
            )
        if self.app_env == "production" and parsed.scheme != "https":
            raise ValueError("S3_PUBLIC_ENDPOINT_URL must use https in production")


@lru_cache
def get_settings() -> Settings:
    return Settings()
