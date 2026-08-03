import os
from functools import lru_cache
from typing import List

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ClientCredential(BaseModel):
    secret: str = Field(min_length=16)
    subject: str
    scopes: set[str] = set()


class Settings(BaseSettings):
    environment: str = "development"
    database_url: str = "sqlite:///./tastegraph.db"
    app_secret: str = "change-me"
    development_api_key: str = "dev-secret"
    client_api_keys: dict[str, ClientCredential] = {}
    public_base_url: str = "http://127.0.0.1:8000"
    allowed_hosts: List[str] = ["127.0.0.1", "localhost", "testserver"]
    cors_origins: List[str] = ["http://127.0.0.1:8000", "http://localhost:8000"]
    log_level: str = "INFO"
    max_request_bytes: int = 1_000_000
    oauth_owner_user_id: str | None = None
    oauth_connection_code: str = "connect-dev"
    oauth_access_token_minutes: int = 60
    oauth_refresh_token_days: int = 30

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

    @field_validator("allowed_hosts", "cors_origins", mode="before")
    @classmethod
    def split_csv(cls, value):
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        return value

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("DATABASE_URL is empty")
        url = value.strip()
        if len(url) >= 2 and url[0] == url[-1] and url[0] in {"'", '"'}:
            url = url[1:-1].strip()
        if "${{" in url or "}}" in url:
            raise ValueError(
                "DATABASE_URL contains an unresolved Railway reference; "
                "add it to the API service with Add Reference -> Postgres -> DATABASE_URL"
            )
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url.removeprefix("postgres://")
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
        if not url.startswith(("sqlite://", "postgresql+psycopg://")):
            scheme = url.partition(":")[0] or "missing"
            raise ValueError(f"DATABASE_URL has an unsupported or malformed scheme: {scheme!r}")
        return url

    @model_validator(mode="after")
    def prevent_ephemeral_railway_sqlite(self):
        if os.getenv("RAILWAY_ENVIRONMENT") and self.database_url.startswith("sqlite"):
            raise ValueError("Railway is using SQLite. Add a Postgres DATABASE_URL reference to the API service.")
        if self.environment.lower() == "production":
            if self.oauth_connection_code == "connect-dev" or len(self.oauth_connection_code) < 20:
                raise ValueError("OAUTH_CONNECTION_CODE must be a long private value in production")
            if self.app_secret == "change-me" or len(self.app_secret) < 32:
                raise ValueError("APP_SECRET must be a long random value in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

