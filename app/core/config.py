from functools import lru_cache
from typing import List
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    environment: str = "development"
    database_url: str = "sqlite:///./tastegraph.db"
    app_secret: str = "change-me"
    development_api_key: str = "dev-secret"
    public_base_url: str = "http://127.0.0.1:8000"
    allowed_hosts: List[str] = ["127.0.0.1", "localhost", "testserver"]
    cors_origins: List[str] = ["http://127.0.0.1:8000", "http://localhost:8000"]
    log_level: str = "INFO"
    max_request_bytes: int = 1_000_000

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

    @field_validator("allowed_hosts", "cors_origins", mode="before")
    @classmethod
    def split_csv(cls, value):
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        return value

@lru_cache
def get_settings() -> Settings:
    return Settings()
