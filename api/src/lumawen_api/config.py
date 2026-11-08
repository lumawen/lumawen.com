"""Settings, read from environment variables (or api/.env on the server)."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# api/.env, found from this file so it loads no matter which folder pm2 starts the API in.
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_prefix="LUMAWEN_", extra="ignore")

    # Defaults to production so a missing setting never exposes dev-only pages like /api/docs.
    env: str = "production"  # development | test | production
    database_url: str = "postgresql+psycopg://lumawen@localhost:5432/lumawen"
    public_base_url: str = "http://localhost:3040"
    build_info_path: str = "build-info.json"

    # Sign-in
    session_days: int = 30  # web cookie and app refresh token lifetime
    access_minutes: int = 15  # app access token lifetime
    # Wrong passwords: 5 from one IP for one email pause that pair for 15 minutes; 50 from
    # anywhere for one email pause that email for an hour. Unknown emails count the same way.
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    max_failed_per_email: int = 50
    email_lockout_minutes: int = 60
    emails_per_hour: int = 3  # per address and email kind (confirm, reset, ...)

    # Email. "console" only logs messages; "smtp" sends them (Purelymail on cobalt).
    mail_backend: str = "console"  # console | smtp
    mail_from: str = "Lumawen <hello@lumawen.com>"
    smtp_host: str = "smtp.purelymail.com"
    smtp_port: int = 465  # 465 = TLS from the start; 587 = STARTTLS
    smtp_username: str = ""
    smtp_password: str = ""
    mail_per_second: float = 2.0

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def dashboard_url(self) -> str:
        return self.public_base_url.rstrip("/") + "/dashboard"


@lru_cache
def get_settings() -> Settings:
    return Settings()
