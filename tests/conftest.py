import os
os.environ["DATABASE_URL"]="sqlite:///./test_tastegraph.db"
os.environ["DEVELOPMENT_API_KEY"]="test-secret"
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from alembic import command
from alembic.config import Config

@pytest.fixture(scope="session",autouse=True)
def migrate_db():
    p=Path("test_tastegraph.db")
    if p.exists():p.unlink()
    cfg=Config("alembic.ini")
    command.upgrade(cfg,"head")
    from scripts.seed import run as seed
    seed()
    yield
    if p.exists():p.unlink()

@pytest.fixture()
def client():
    from app.main import app
    with TestClient(app) as c:yield c

@pytest.fixture()
def auth():return {"X-API-Key":"test-secret","X-Client-ID":"pytest"}
