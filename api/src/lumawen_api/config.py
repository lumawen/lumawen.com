"""Settings, read from environment variables (or api/.env on the server)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="LUMAWEN_", extra="ignore")

    env: str = "development"  # development | test | production
    database_url: str = "postgresql+psycopg://lumawen@localhost:5432/lumawen"
    public_base_url: str = "http://localhost:3040"
    build_info_path: str = "build-info.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
