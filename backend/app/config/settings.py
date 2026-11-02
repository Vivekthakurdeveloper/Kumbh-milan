from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDER_MARKERS = ("change-me", "changeme", "placeholder", "example")


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

    case_number_prefix: str = "KSLF"
    idempotency_ttl_hours: int = 24

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

    retention_archive_after_days: int = 90
    retention_anonymize_after_days: int = 365
    retention_attachment_purge_after_days: int = 30
    retention_max_items_per_run: int = 5000
    retention_database_url: str | None = Field(
        None, description="Owner connection for executing anonymisation runs (retention task only, never the API)"
    )

    notification_provider: Literal["disabled", "console", "mock"] = Field(
        "disabled",
        description="console or mock are local only. No real SMS/WhatsApp provider is integrated.",
    )
    notification_max_attempts: int = 5
    notification_retry_base_seconds: int = 60
    notification_stale_after_seconds: int = 300
    notification_default_country_code: str = "+91"

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
    rate_limit_upload_per_minute: int = 30
    rate_limit_case_create_per_minute: int = 60
    rate_limit_search_per_minute: int = 180
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
            if self.notification_provider != "disabled":
                raise ValueError("NOTIFICATION_PROVIDER must be disabled outside local and test until a provider is approved")
            if not self.workers_required:
                raise ValueError("WORKERS_REQUIRED must be true so readiness checks supervised workers")
            if self.scan_mode != "clamav":
                raise ValueError("SCAN_MODE must be clamav outside local and test; unscanned files are never approved")
            if len(self.jwt_secret) < 32 or any(m in self.jwt_secret.lower() for m in PLACEHOLDER_MARKERS):
                raise ValueError("JWT_SECRET must be a strong, non-placeholder secret of at least 32 characters")
            if not self.contact_encryption_key:
                raise ValueError("CONTACT_ENCRYPTION_KEY is required outside local and test environments")
            if self.docs_enabled and self.app_env == "production":
                raise ValueError("DOCS_ENABLED must be false in production")
            if self.rate_limit_backend != "redis" or not self.redis_url:
                raise ValueError("RATE_LIMIT_BACKEND=redis and REDIS_URL are required so limits hold across replicas")
            if not self.metrics_token or len(self.metrics_token) < 24:
                raise ValueError("METRICS_TOKEN (24+ characters) is required to protect /metrics")
            if "*" in self.cors_origins:
                raise ValueError("CORS_ORIGINS must list explicit origins outside local development")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
