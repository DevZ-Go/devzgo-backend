"""
API test fixtures. They only run when TEST_DATABASE_URL points at a throwaway database
whose name ends in "_test" (e.g. the local compose db), so the shared database is never touched:

  docker compose exec db createdb -U devzgo devzgo_test
  docker compose exec -e TEST_DATABASE_URL=postgresql://devzgo:devzgo@db:5432/devzgo_test backend pytest -q
"""

import os
import uuid

import pytest

TEST_DB = os.getenv("TEST_DATABASE_URL", "")
if TEST_DB:
    if not TEST_DB.rsplit("/", 1)[-1].split("?")[0].endswith("_test"):
        raise RuntimeError("TEST_DATABASE_URL must point at a database whose name ends in _test")
    os.environ["DATABASE_URL"] = TEST_DB
    os.environ.setdefault("SECRET_KEY", "test-secret")


@pytest.fixture(scope="session")
def client():
    if not TEST_DB:
        pytest.skip("TEST_DATABASE_URL not set")
    from fastapi.testclient import TestClient
    from app.db.base import Base
    from app.db.session import engine
    from app.main import app
    from app.scripts.bootstrap_db import _ensure_indexes

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    _ensure_indexes()
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def make_user(client):
    """Register a fresh user and return (auth headers, user id)."""
    def _make():
        name = "u" + uuid.uuid4().hex[:10]
        client.post("/auth/register", json={"username": name, "email": f"{name}@example.com", "password": "password123"})
        token = client.post("/auth/login", data={"username": f"{name}@example.com", "password": "password123"}).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        return headers, client.get("/auth/me", headers=headers).json()["id"]
    return _make
