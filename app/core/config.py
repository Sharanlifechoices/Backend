"""Application configuration loaded from environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # General
    env: Literal["development", "production", "test"] = "development"
    frontend_url: str = "http://localhost:5173"
    # Comma-separated extra CORS origins (e.g. the Vercel preview domain)
    extra_cors_origin: str = ""
    # Public URL of this API (used in links inside emails, e.g. unsubscribe)
    public_api_url: str = "http://localhost:8000"

    # Supabase
    supabase_url: str = Field(default="", validation_alias="SUPABASE_URL")
    supabase_anon_key: str = Field(default="", validation_alias="SUPABASE_ANON_KEY")
    supabase_service_role_key: str = Field(default="", validation_alias="SUPABASE_SERVICE_ROLE_KEY")
    # Only set if the project has NOT migrated to asymmetric JWKS signing keys.
    supabase_jwt_secret: str = Field(default="", validation_alias="SUPABASE_JWT_SECRET")
    supabase_http_timeout: float = Field(default=30.0, validation_alias="SUPABASE_HTTP_TIMEOUT")

    # Email. mock = log the email instead of sending (default until Resend is wired).
    email_mode: Literal["mock", "resend"] = "mock"
    resend_api_key: str = Field(default="", validation_alias="RESEND_API_KEY")
    resend_from_email: str = Field(default="noreply@example.com", validation_alias="RESEND_FROM_EMAIL")
    resend_from_name: str = Field(default="My LifeChoices", validation_alias="RESEND_FROM_NAME")
    # Replies land in the Zoho-hosted mailbox
    email_reply_to: str = Field(default="", validation_alias="EMAIL_REPLY_TO")

    # OTP
    otp_secret: str = Field(default="change-me-in-prod", validation_alias="OTP_SECRET")
    otp_ttl_seconds: int = 600
    otp_max_attempts: int = 5
    otp_resend_cooldown_seconds: int = 60

    # Admin (shared secret sent as X-Admin-Secret) and scheduled-job secret
    admin_secret: str = Field(default="", validation_alias="ADMIN_SECRET")
    jobs_secret: str = Field(default="", validation_alias="JOBS_SECRET")

    # Razorpay (server-side orders + signature verification)
    razorpay_key_id: str = Field(default="", validation_alias="RAZORPAY_KEY_ID")
    razorpay_key_secret: str = Field(default="", validation_alias="RAZORPAY_KEY_SECRET")
    razorpay_webhook_secret: str = Field(default="", validation_alias="RAZORPAY_WEBHOOK_SECRET")

    # Logging
    log_level: str = "INFO"
    log_file: str = "logs/api.log"  # empty string disables the rotating file handler
    log_body_max_chars: int = 2000

    @property
    def cors_origins(self) -> list[str]:
        extras = [o.strip() for o in self.extra_cors_origin.split(",") if o.strip()]
        return [self.frontend_url, *extras]

    @property
    def resend_from(self) -> str:
        return f"{self.resend_from_name} <{self.resend_from_email}>"

    @property
    def jwks_url(self) -> str:
        return f"{self.supabase_url}/auth/v1/.well-known/jwks.json"

    @property
    def jwt_issuer(self) -> str:
        return f"{self.supabase_url}/auth/v1"

    @property
    def razorpay_enabled(self) -> bool:
        return bool(self.razorpay_key_id and self.razorpay_key_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
