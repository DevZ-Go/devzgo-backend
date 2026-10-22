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
                      json={"username": name.upper(), "role": "Designer", "access": "editor"}).json()
    again = client.post(f"{_base(pid)}/invitations", headers=owner, json={"username": name})
    assert again.status_code == 409 and again.json()["code"] == "invitation_pending"

    assert client.get(f"/projects/{pid}", headers=guest).status_code == 403  # not a member yet
    assert client.get(f"/profiles/{guest_id}/collaborations", headers=owner).json() == []
    assert client.get(f"{_base(pid)}/me", headers=guest).json()["status"] == "invited"
    assert client.post(f"{_base(pid)}/requests", headers=guest, json={}).status_code == 404  # private stays hidden

    assert [i["id"] for i in client.get("/collaborations/invitations/mine", headers=guest).json()] == [inv["id"]]
    assert client.put(f"/collaborations/invitations/{inv['id']}/accept", headers=guest).json()["permissions"]["can_manage_files"]
    access = client.get(f"/projects/{pid}", headers=guest).json()["access"]
    assert access["view"] and access["files"] and access["edit"] and access["comment"] and not access["secrets"]
    assert not access["owner"] and not access["manage_team"]
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

    assert client.put(f"/projects/{pid}", headers=member, json={"title": "Mine", "category": "Gaming", "visibility": "Public"}).status_code == 403
    patch = client.patch(f"{_base(pid)}/members/{member_id}", headers=owner, json={"access": "editor"})
    assert patch.json()["access"] == "editor" and patch.json()["permissions"]["can_edit_details"]
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
    joined = client.put(f"/collaborations/requests/{req['id']}/accept", headers=owner, json={"access": "manager"}).json()
    assert joined["permissions"]["can_view_secrets"] and joined["permissions"]["can_manage_team"] and joined["user_id"] == asker_id
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


def test_incoming_requests_list_only_the_owners_pending_ones(client, make_user):
    owner, _ = make_user()
    other, _ = make_user()
    asker, _ = make_user()
    mine = _project(client, owner, visibility="Public")
    theirs = _project(client, other, visibility="Public")
    req = client.post(f"{_base(mine)}/requests", headers=asker, json={"role": "Designer"}).json()
    client.post(f"{_base(theirs)}/requests", headers=asker, json={})

    incoming = client.get("/collaborations/requests/incoming", headers=owner).json()
    assert [r["id"] for r in incoming] == [req["id"]]
    assert incoming[0]["project_title"] == "Team"
    assert client.put(f"/collaborations/requests/{req['id']}/decline", headers=owner).status_code == 200
    assert client.get("/collaborations/requests/incoming", headers=owner).json() == []


def _add(client, owner, pid, member, access):
    inv = client.post(f"{_base(pid)}/invitations", headers=owner, json={"username": _username(client, member), "access": access}).json()
    assert inv["access"] == access
    return client.put(f"/collaborations/invitations/{inv['id']}/accept", headers=member).json()


def test_viewers_read_but_do_not_comment(client, make_user):
    owner, _ = make_user()
    viewer, _ = make_user()
    pid = _project(client, owner)
    joined = _add(client, owner, pid, viewer, "viewer")
    assert joined["access"] == "viewer" and not joined["permissions"]["can_comment"]

    access = client.get(f"/projects/{pid}", headers=viewer).json()["access"]
    assert access["view"] and not access["comment"] and not access["edit"]
    assert client.post(f"/projects/{pid}/comments", headers=viewer, json={"body": "Hello"}).status_code == 403
    assert client.get(f"/projects/{pid}/comments", headers=viewer).status_code == 200


def test_managers_grow_the_team_but_only_the_owner_makes_managers(client, make_user):
    owner, _ = make_user()
    manager, _ = make_user()
    guest, guest_id = make_user()
    asker, _ = make_user()
    pid = _project(client, owner, "Public")
    _add(client, owner, pid, manager, "manager")

    assert client.get(f"{_base(pid)}/invitee-search?q={_username(client, guest)[:6]}", headers=manager).status_code == 200
    r = client.post(f"{_base(pid)}/invitations", headers=manager, json={"username": _username(client, guest), "access": "manager"})
    assert r.status_code == 403
    inv = client.post(f"{_base(pid)}/invitations", headers=manager, json={"username": _username(client, guest), "access": "editor"}).json()
    assert [i["id"] for i in client.get(f"{_base(pid)}/invitations", headers=manager).json()] == [inv["id"]]
    assert client.delete(f"/collaborations/invitations/{inv['id']}", headers=manager).status_code == 200

    req = client.post(f"{_base(pid)}/requests", headers=asker, json={"role": "Tester"}).json()
    assert [q["id"] for q in client.get(f"{_base(pid)}/requests", headers=manager).json()] == [req["id"]]
    joined = client.put(f"/collaborations/requests/{req['id']}/accept", headers=manager, json={"access": "commenter"}).json()
    assert joined["access"] == "commenter" and joined["role"] == "Tester"

    assert client.patch(f"{_base(pid)}/members/{guest_id}", headers=manager, json={"access": "editor"}).status_code == 403
    assert client.get(f"{_base(pid)}/requests", headers=guest).status_code in (403, 404)


def test_each_person_writes_only_their_own_focus(client, make_user):
    owner, _ = make_user()
    member, member_id = make_user()
    stranger, _ = make_user()
    pid = _project(client, owner, "Public")
    _add(client, owner, pid, member, "editor")

    assert client.patch(f"{_base(pid)}/focus", headers=member, json={"focus": "  Login screen  "}).json() == {"focus": "Login screen"}
    assert client.patch(f"{_base(pid)}/focus", headers=owner, json={"focus": "API design"}).status_code == 200
    assert client.patch(f"{_base(pid)}/focus", headers=stranger, json={"focus": "Hijack"}).status_code == 403

    team = {m["user_id"]: m for m in client.get(f"{_base(pid)}/collaborators").json()}
    assert team[member_id]["focus"] == "Login screen"
    assert next(m for m in team.values() if m["is_owner"])["focus"] == "API design"
    assert client.get(f"{_base(pid)}/me", headers=member).json()["focus"] == "Login screen"
    assert client.patch(f"{_base(pid)}/focus", headers=member, json={"focus": ""}).json() == {"focus": None}


def test_profile_lists_joined_projects_with_access_focus_and_team(client, make_user):
    owner, _ = make_user()
    member, member_id = make_user()
    pid = client.post("/projects", headers=owner, json={"title": "Joined", "category": "Gaming", "visibility": "Public"}).json()["id"]
    name = client.get("/auth/me", headers=member).json()["username"]
    inv = client.post(f"/collaborations/projects/{pid}/invitations", headers=owner,
                      json={"username": name, "role": "Frontend", "access": "editor"}).json()
    client.put(f"/collaborations/invitations/{inv['id']}/accept", headers=member)
    client.patch(f"/collaborations/projects/{pid}/focus", headers=member, json={"focus": "Login screen"})

    joined = client.get(f"/profiles/{member_id}/collaborations", headers=owner).json()
    assert len(joined) == 1
    row = joined[0]
    assert row["contribution_info"] == "Frontend" and row["access"] == "editor" and row["focus"] == "Login screen"
    assert {m["access"] for m in row["team"]} == {"owner", "editor"}
    assert row["joined_at"]
