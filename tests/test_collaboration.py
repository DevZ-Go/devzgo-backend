"""Join requests vs invitations, owner-chosen permissions, leave/remove."""

from sqlalchemy import text


def _project(client, headers, visibility="Private"):
    return client.post("/projects", headers=headers, json={"title": "Team", "category": "Gaming", "visibility": visibility}).json()["id"]


def _username(client, headers):
    return client.get("/auth/me", headers=headers).json()["username"]


def _base(pid):
    return f"/collaborations/projects/{pid}"


def test_invitation_gives_membership_only_after_acceptance(client, make_user):
    owner, _ = make_user()
    guest, guest_id = make_user()
    pid = _project(client, owner)
    name = _username(client, guest)

    found = client.get(f"{_base(pid)}/invitee-search?q={name[:6]}", headers=owner).json()
    assert {"id": guest_id, "status": "none"}.items() <= next(u for u in found if u["id"] == guest_id).items()
    inv = client.post(f"{_base(pid)}/invitations", headers=owner,
                      json={"username": name.upper(), "role": "Designer", "permissions": {"can_manage_files": True}}).json()
    again = client.post(f"{_base(pid)}/invitations", headers=owner, json={"username": name})
    assert again.status_code == 409 and again.json()["code"] == "invitation_pending"

    assert client.get(f"/projects/{pid}", headers=guest).status_code == 403  # not a member yet
    assert client.get(f"/profiles/{guest_id}/collaborations", headers=owner).json() == []
    assert client.get(f"{_base(pid)}/me", headers=guest).json()["status"] == "invited"
    assert client.post(f"{_base(pid)}/requests", headers=guest, json={}).status_code == 404  # private stays hidden

    assert [i["id"] for i in client.get("/collaborations/invitations/mine", headers=guest).json()] == [inv["id"]]
    assert client.put(f"/collaborations/invitations/{inv['id']}/accept", headers=guest).json()["permissions"]["can_manage_files"]
    access = client.get(f"/projects/{pid}", headers=guest).json()["access"]
    assert access["view"] and access["files"] and not access["edit"] and not access["owner"]
    assert client.put(f"/projects/{pid}", headers=guest, json={"title": "Mine", "category": "Gaming", "visibility": "Public"}).status_code == 403
    assert [p["id"] for p in client.get(f"/profiles/{guest_id}/collaborations", headers=owner).json()] == [pid]
    assert client.get(f"/profiles/{guest_id}/collaborations").json() == []  # private project stays hidden from strangers

    r = client.post(f"{_base(pid)}/invitations", headers=owner, json={"username": name})
    assert r.status_code == 409 and r.json()["code"] == "already_member"


def test_editor_permission_and_member_management(client, make_user):
    owner, _ = make_user()
    member, member_id = make_user()
    pid = _project(client, owner)
    inv = client.post(f"{_base(pid)}/invitations", headers=owner, json={"username": _username(client, member)}).json()
    client.put(f"/collaborations/invitations/{inv['id']}/accept", headers=member)

    patch = client.patch(f"{_base(pid)}/members/{member_id}", headers=owner, json={"can_edit_details": True})
    assert patch.json()["permissions"]["can_edit_details"]
    body = client.put(f"/projects/{pid}", headers=member, json={"title": "Renamed", "category": "Gaming", "visibility": "Public"}).json()
    assert body["title"] == "Renamed" and body["visibility"] == "Private"  # members cannot change visibility

    assert client.patch(f"{_base(pid)}/members/{member_id}", headers=member, json={}).status_code == 403
    assert client.delete(f"{_base(pid)}/members/me", headers=owner).status_code == 400
    assert client.delete(f"{_base(pid)}/members/{member_id}", headers=owner).status_code == 200
    assert client.get(f"/projects/{pid}", headers=member).status_code == 403


def test_request_rules_withdraw_and_leave(client, make_user):
    owner, _ = make_user()
    asker, asker_id = make_user()
    pid = _project(client, owner, "Public")

    req = client.post(f"{_base(pid)}/requests", headers=asker, json={"role": "Backend"}).json()
    dup = client.post(f"{_base(pid)}/requests", headers=asker, json={})
    assert dup.status_code == 409 and dup.json()["code"] == "request_pending"
    r = client.post(f"{_base(pid)}/invitations", headers=owner, json={"username": _username(client, asker)})
    assert r.status_code == 409 and r.json()["code"] == "request_pending" and r.json()["request_id"] == req["id"]

    assert client.delete(f"/collaborations/requests/{req['id']}", headers=asker).status_code == 200
    assert client.get(f"{_base(pid)}/requests", headers=owner).json() == []

    req = client.post(f"{_base(pid)}/requests", headers=asker, json={}).json()
    joined = client.put(f"/collaborations/requests/{req['id']}/accept", headers=owner, json={"can_view_secrets": True}).json()
    assert joined["permissions"]["can_view_secrets"] and joined["user_id"] == asker_id
    assert client.get(f"{_base(pid)}/me", headers=asker).json()["status"] == "member"
    assert client.delete(f"{_base(pid)}/members/me", headers=asker).status_code == 200
    assert client.get(f"{_base(pid)}/me", headers=asker).json()["status"] == "none"

    client.post(f"{_base(pid)}/invitations", headers=owner, json={"username": _username(client, asker)})
    r = client.post(f"{_base(pid)}/requests", headers=asker, json={})
    assert r.status_code == 409 and r.json()["code"] == "invitation_pending"


def test_expired_and_revoked_invitations(client, make_user):
    from app.db.session import engine

    owner, _ = make_user()
    guest, _ = make_user()
    pid = _project(client, owner)
    name = _username(client, guest)
    inv = client.post(f"{_base(pid)}/invitations", headers=owner, json={"username": name}).json()
    assert client.delete(f"/collaborations/invitations/{inv['id']}", headers=guest).status_code == 403
    assert client.delete(f"/collaborations/invitations/{inv['id']}", headers=owner).status_code == 200
    assert client.put(f"/collaborations/invitations/{inv['id']}/accept", headers=guest).status_code == 400

    inv = client.post(f"{_base(pid)}/invitations", headers=owner, json={"username": name}).json()
    with engine.begin() as conn:
        conn.execute(text("UPDATE project_invitations SET expires_at = now() - interval '1 day' WHERE id = :id"), {"id": inv["id"]})
    assert client.get("/collaborations/invitations/mine", headers=guest).json() == []
    assert client.put(f"/collaborations/invitations/{inv['id']}/accept", headers=guest).status_code == 400
    assert client.post(f"{_base(pid)}/invitations", headers=owner, json={"username": name}).status_code == 200
