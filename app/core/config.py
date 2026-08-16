from functools import lru_cache
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["local", "staging", "prod"] = "local"
    revision: str = "dev"
    openai_api_key: str
    postgres_dsn: str
    redis_dsn: str


@lru_cache
def get_settings() -> Settings:
    """Cached so the .env file is parsed once, not per request."""
    return Settings()