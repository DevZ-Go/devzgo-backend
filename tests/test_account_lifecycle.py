"""Pause until next sign-in, 30-day scheduled deletion, and purge after the grace window."""

from datetime import datetime, timedelta, timezone


def test_deactivate_hides_until_login(client, make_user):
    headers, user_id = make_user()
    me = client.get("/auth/me", headers=headers).json()
    password = "Password1!"

    paused = client.post("/auth/deactivate", headers=headers, json={"password": password})
    assert paused.status_code == 200
    assert paused.json()["deactivated_at"] is not None
    assert client.get("/auth/me", headers=headers).status_code == 401

    found = client.get("/profiles/search", params={"query": me["username"]}).json()
    assert all(row["id"] != user_id for row in found)

    login = client.post("/auth/login", data={"username": me["email"], "password": password})
    assert login.status_code == 200
    fresh = {"Authorization": "Bearer " + login.json()["access_token"]}
    restored = client.get("/auth/me", headers=fresh).json()
    assert restored["deactivated_at"] is None and restored["delete_after"] is None


def test_schedule_delete_cancels_on_login(client, make_user):
    headers, user_id = make_user()
    me = client.get("/auth/me", headers=headers).json()
    password = "Password1!"

    bad = client.post(
        "/auth/schedule-delete",
        headers=headers,
        json={"password": password, "username": "wrong_name"},
    )
    assert bad.status_code == 400

    scheduled = client.post(
        "/auth/schedule-delete",
        headers=headers,
        json={"password": password, "username": me["username"]},
    )
    assert scheduled.status_code == 200
    assert scheduled.json()["delete_after"] is not None
    assert client.get("/auth/me", headers=headers).status_code == 401

    found = client.get("/profiles/search", params={"query": me["username"]}).json()
    assert all(row["id"] != user_id for row in found)

    login = client.post("/auth/login", data={"username": me["email"], "password": password})
    assert login.status_code == 200
    fresh = {"Authorization": "Bearer " + login.json()["access_token"]}
    restored = client.get("/auth/me", headers=fresh).json()
    assert restored["delete_after"] is None and restored["deactivated_at"] is None


def test_purge_after_delete_after(client, make_user):
    headers, user_id = make_user()
    me = client.get("/auth/me", headers=headers).json()
    password = "Password1!"

    assert client.post(
        "/auth/schedule-delete",
        headers=headers,
        json={"password": password, "username": me["username"]},
    ).status_code == 200

    from app.db.session import SessionLocal
    from app.models.user import User
    from app.services import account_lifecycle

    with SessionLocal() as db:
        user = db.query(User).filter(User.id == user_id).one()
        user.delete_after = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
        assert account_lifecycle.purge_due_accounts(db) == 1
        assert db.query(User).filter(User.id == user_id).first() is None

    assert client.post("/auth/login", data={"username": me["email"], "password": password}).status_code == 401
