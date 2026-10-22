"""
API test fixtures. They only run when TEST_DATABASE_URL points at a throwaway database
whose name ends in "_test" (e.g. the local compose db), so the shared database is never touched:

  docker compose exec db createdb -U devzgo devzgo_test
  docker compose exec -e TEST_DATABASE_URL=postgresql://devzgo:devzgo@db:5432/devzgo_test backend pytest -q
"""

import os
import uuid
from pathlib import Path

# Tests only. A real server must set its own key; this one is 32 zero bytes.
# setdefault would keep an empty value passed in by Compose, which is not a key.
if not os.environ.get("GITHUB_TOKEN_ENCRYPTION_KEY") and not os.environ.get("WORKSPACE_TOKEN_KEY"):
    os.environ["GITHUB_TOKEN_ENCRYPTION_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

import pytest

TEST_DB = os.getenv("TEST_DATABASE_URL", "")
if TEST_DB:
    if not TEST_DB.rsplit("/", 1)[-1].split("?")[0].endswith("_test"):
        raise RuntimeError("TEST_DATABASE_URL must point at a database whose name ends in _test")
    os.environ["DATABASE_URL"] = TEST_DB
    os.environ["STORAGE_BACKEND"] = "local"
    os.environ.setdefault("SECRET_KEY", "test-secret")
    os.environ["WRAP_JOB"] = "off"  # tests call the generator directly


@pytest.fixture(scope="session")
def client():
    if not TEST_DB:
        pytest.skip("TEST_DATABASE_URL not set")
    from fastapi.testclient import TestClient
    from app.db.session import engine
    from app.main import app

    reset_schema(engine)
    with TestClient(app) as c:
        yield c


def reset_schema(engine):
    """Empty the test database and build it through the real migrations."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")


@pytest.fixture
def make_user(client):
    """Register a fresh user and return (auth headers, user id)."""
    def _make():
        name = "u" + uuid.uuid4().hex[:10]
        client.post("/auth/register", json={"username": name, "email": f"{name}@example.com", "password": "Password1!"})
        token = client.post("/auth/login", data={"username": f"{name}@example.com", "password": "Password1!"}).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        return headers, client.get("/auth/me", headers=headers).json()["id"]
    return _make


@pytest.fixture
def make_recruiter(client):
    """Register a fresh recruiter account and return (auth headers, user id)."""
    def _make(company: str = "North Agency"):
        name = "r" + uuid.uuid4().hex[:10]
        client.post("/auth/register", json={"username": name, "email": f"{name}@example.com", "password": "Password1!",
                                            "role": "Recruiter", "company": company})
        token = client.post("/auth/login", data={"username": name, "password": "Password1!"}).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        return headers, client.get("/auth/me", headers=headers).json()["id"]
    return _make
