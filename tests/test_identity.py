"""Username and password rules, availability, change/set password and token revocation."""

import pytest

from app.core.policy import password_problem


@pytest.mark.parametrize("pw,ok", [("Password1!", True), ("password1!", False), ("Password12", False),
                                   ("Pa!1", False), ("P!" + "a" * 127, False), ("Ünïcode_pass", True)])
def test_password_policy(pw, ok):
    assert (password_problem(pw) is None) is ok


def test_username_rules_and_availability(client, make_user):
    def check(name):
        return client.get("/auth/username-available", params={"username": name}).json()

    assert check("  New.Dev_1 ") == {"username": "new.dev_1", "available": True, "reason": None}
    assert check("ab")["reason"] == check(".dot")["reason"] == check("bad name")["reason"] == "invalid"
    assert check("Admin")["reason"] == "reserved"

    body = {"username": "Taken_Name", "email": "taken@example.com", "password": "Password1!"}
    created = client.post("/auth/register", json=body)
    assert created.status_code == 200 and created.json()["username"] == "taken_name"
    assert check("TAKEN_name") == {"username": "taken_name", "available": False, "reason": "taken"}
    assert client.post("/auth/register", json={**body, "email": "t2@example.com", "username": "TAKEN_NAME"}).status_code == 409
    assert client.post("/auth/register", json={**body, "username": "admin", "email": "t3@example.com"}).status_code == 422
    assert client.post("/auth/register", json={**body, "username": "weakpw", "email": "t4@example.com",
                                               "password": "password1"}).status_code == 422


def test_change_password_revokes_old_tokens(client, make_user):
    headers, _ = make_user()
    email = client.get("/auth/me", headers=headers).json()["email"]
    assert client.post("/auth/change-password", headers=headers,
                       json={"current_password": "wrong", "new_password": "Changed1!"}).status_code == 400
    assert client.post("/auth/change-password", headers=headers,
                       json={"current_password": "Password1!", "new_password": "weak"}).status_code == 422

    changed = client.post("/auth/change-password", headers=headers,
                          json={"current_password": "Password1!", "new_password": "Changed1!"})
    assert changed.status_code == 200
    assert client.get("/auth/me", headers=headers).status_code == 401  # other sessions are signed out
    assert client.get("/auth/me", headers={"Authorization": "Bearer " + changed.json()["access_token"]}).status_code == 200
    assert client.post("/auth/login", data={"username": email, "password": "Changed1!"}).status_code == 200


def test_github_only_account_can_set_password(client):
    from app.core.security import issue_token
    from app.db.session import SessionLocal
    from app.models.user import User

    with SessionLocal() as db:
        user = User(username="ghonly", email="ghonly@example.com", github_id="4242", password_hash=None)
        db.add(user)
        db.commit()
        headers = {"Authorization": "Bearer " + issue_token(user)}
    set_pw = client.post("/auth/change-password", headers=headers, json={"new_password": "FirstPass1!"})
    assert set_pw.status_code == 200
    assert client.post("/auth/login", data={"username": "ghonly@example.com", "password": "FirstPass1!"}).status_code == 200


def test_login_accepts_email_or_username_in_any_case(client):
    client.post("/auth/register", json={"username": "either_way", "email": "either@example.com", "password": "Password1!"})
    for login_id in ("either@example.com", "EITHER@example.com", "either_way", "Either_Way", "@either_way"):
        response = client.post("/auth/login", data={"username": login_id, "password": "Password1!"})
        assert response.status_code == 200, login_id
    assert client.post("/auth/login", data={"username": "either_way", "password": "Wrong1!pass"}).status_code == 401
    assert client.post("/auth/login", data={"username": "nobody_here", "password": "Password1!"}).status_code == 401
