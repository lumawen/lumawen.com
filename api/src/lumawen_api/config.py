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


@lru_cache
def get_settings() -> Settings:
    return Settings()
