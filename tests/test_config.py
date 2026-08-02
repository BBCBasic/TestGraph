import pytest
from pydantic import ValidationError

from app.core.config import Settings


@pytest.mark.parametrize("scheme", ["postgres://", "postgresql://"])
def test_railway_postgres_url_uses_psycopg3(scheme):
    settings = Settings(database_url=f"{scheme}user:password@db:5432/app")
    assert settings.database_url == "postgresql+psycopg://user:password@db:5432/app"


def test_database_url_trims_quotes_and_whitespace():
    settings = Settings(database_url='  "postgresql://user:password@db:5432/app"  ')
    assert settings.database_url == "postgresql+psycopg://user:password@db:5432/app"


def test_unresolved_railway_reference_has_clear_error():
    with pytest.raises(ValidationError, match="unresolved Railway reference"):
        Settings(database_url="${{Postgres.DATABASE_URL}}")


def test_railway_cannot_silently_use_sqlite(monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    with pytest.raises(ValidationError, match="Railway is using SQLite"):
        Settings(database_url="sqlite:///./tastegraph.db")
