"""Admin access, hidden secrets, and changes that wait for more than one admin."""

import json


def _register(client, username, email):
    created = client.post("/auth/register", json={"username": username, "email": email, "password": "Password1!"})
    assert created.status_code == 200, created.text
    token = client.post("/auth/login", data={"username": email, "password": "Password1!"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    return headers, me.json()


def test_stranger_cannot_open_admin(client, make_user):
    headers, _user_id = make_user()
    assert client.get("/admin/session", headers=headers).status_code == 403
    assert client.get("/admin/tables/users", headers=headers).status_code == 403


def test_owner_team_quorum_and_secrets(client):
    owner_headers, owner = _register(client, "devzowner", "idevzgo.ca@gmail.com")
    assert owner["is_platform_admin"] is True
    assert owner["admin_role"] == "owner"
    assert client.get("/admin/session", headers=owner_headers).json()["role"] == "owner"

    _victim_headers, victim = _register(client, "victimone", "victimone@example.com")
    blocked = client.post("/admin/actions", headers=owner_headers, json={
        "action": "user.purge", "target_id": victim["id"], "payload": {},
        "reason": "This account is spam", "typed": victim["email"],
    })
    assert blocked.status_code == 400, blocked.text
    assert "at least two admins" in blocked.json()["detail"]

    access = client.get("/admin/access", headers=owner_headers).json()
    invited = {row["email"] for row in access["invites"]}
    assert "singhchangaganjeet@gmail.com" in invited
    assert "rajputmanasi2002@gmail.com" in invited

    second_headers, second = _register(client, "ganjeet", "singhchangaganjeet@gmail.com")
    third_headers, _third = _register(client, "manasi", "rajputmanasi2002@gmail.com")
    assert second["admin_role"] == "admin"
    assert client.get("/admin/session", headers=second_headers).status_code == 200

    mismatch = client.post("/admin/actions", headers=owner_headers, json={
        "action": "user.suspend", "target_id": victim["id"], "payload": {},
        "reason": "Trying the wrong confirmation", "typed": "not-the-email",
    })
    assert mismatch.status_code == 400
    assert client.post("/auth/login", data={"username": victim["email"], "password": "Password1!"}).status_code == 200

    listed = client.get("/admin/tables/users", headers=owner_headers)
    assert listed.status_code == 200, listed.text
    blob = json.dumps(listed.json())
    assert "$2" not in blob
    owner_row = next(row for row in listed.json()["rows"] if row["email"] == "idevzgo.ca@gmail.com")
    assert owner_row["password_hash"] == {"redacted": True, "present": True}

    preview = client.post("/admin/actions/preview", headers=second_headers, json={
        "action": "admin.revoke", "target_id": owner["id"], "payload": {},
    })
    assert preview.status_code == 200, preview.text
    assert preview.json()["blocked"]

    proposed = client.post("/admin/actions", headers=owner_headers, json={
        "action": "user.purge", "target_id": victim["id"], "payload": {},
        "reason": "Spam account removal", "typed": victim["email"],
    })
    assert proposed.status_code == 200, proposed.text
    proposal = proposed.json()
    assert proposal["status"] == "pending"
    assert proposal["required_approvals"] == 3
    assert client.post("/auth/login", data={"username": victim["email"], "password": "Password1!"}).status_code == 200

    waiting = client.post(f"/admin/proposals/{proposal['proposal_id']}/vote", headers=second_headers, json={
        "decision": "approve", "typed": "APPROVE", "reason": "I checked this account",
    })
    assert waiting.status_code == 200, waiting.text
    assert waiting.json()["status"] == "pending"

    finished = client.post(f"/admin/proposals/{proposal['proposal_id']}/vote", headers=third_headers, json={
        "decision": "approve", "typed": "APPROVE", "reason": "Agreed after review",
    })
    assert finished.status_code == 200, finished.text
    assert finished.json()["status"] == "executed"
    assert client.post("/auth/login", data={"username": victim["email"], "password": "Password1!"}).status_code == 401

    _kept_headers, kept = _register(client, "keeptwo", "keeptwo@example.com")
    opened = client.post("/admin/actions", headers=owner_headers, json={
        "action": "user.schedule_delete", "target_id": kept["id"], "payload": {},
        "reason": "We may not need this", "typed": kept["email"],
    })
    assert opened.status_code == 200, opened.text
    stopped = client.post(f"/admin/proposals/{opened.json()['proposal_id']}/vote", headers=third_headers, json={
        "decision": "reject", "typed": "REJECT", "reason": "Keep the account",
    })
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["status"] == "rejected"
    assert client.post("/auth/login", data={"username": kept["email"], "password": "Password1!"}).status_code == 200

    _locked_headers, locked = _register(client, "lockedone", "lockedone@example.com")
    suspended = client.post("/admin/actions", headers=owner_headers, json={
        "action": "user.suspend", "target_id": locked["id"], "payload": {},
        "reason": "Locked for a terms breach", "typed": locked["email"],
    })
    assert suspended.status_code == 200, suspended.text
    assert suspended.json()["status"] == "done"
    refused = client.post("/auth/login", data={"username": locked["email"], "password": "Password1!"})
    assert refused.status_code == 403
    assert "suspended" in refused.json()["detail"]
    restored = client.post("/admin/actions", headers=owner_headers, json={
        "action": "user.unsuspend", "target_id": locked["id"], "payload": {},
        "reason": "The review is finished", "typed": locked["email"],
    })
    assert restored.status_code == 200, restored.text
    assert client.post("/auth/login", data={"username": locked["email"], "password": "Password1!"}).status_code == 200

    granted = client.post("/admin/actions", headers=owner_headers, json={
        "action": "admin.grant", "payload": {"email": "futureadmin@example.com"},
        "reason": "They help run DevZ-Go", "typed": "futureadmin@example.com",
    })
    assert granted.status_code == 200, granted.text
    future_headers, future = _register(client, "futureadm", "futureadmin@example.com")
    assert future["admin_role"] == "admin"
    assert client.get("/admin/catalog", headers=future_headers).status_code == 200
