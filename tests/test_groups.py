"""Group chats: project teams get a room, and people can start one themselves."""


def _project(client, headers):
    return client.post("/projects", headers=headers, json={"title": "Team", "category": "Gaming", "visibility": "Public"}).json()["id"]


def test_collaboration_opens_a_group_and_custom_groups_need_a_connection(client, make_user):
    owner, _owner_id = make_user()
    guest, guest_id = make_user()
    stranger, stranger_id = make_user()
    pid = _project(client, owner)
    name = client.get("/auth/me", headers=guest).json()["username"]

    assert client.get("/messages/groups", headers=owner).json() == []
    inv = client.post(f"/collaborations/projects/{pid}/invitations", headers=owner, json={"username": name}).json()
    assert client.put(f"/collaborations/invitations/{inv['id']}/accept", headers=guest).status_code == 200

    owner_rooms = client.get("/messages/groups", headers=owner).json()
    guest_rooms = client.get("/messages/groups", headers=guest).json()
    assert len(owner_rooms) == len(guest_rooms) == 1
    assert owner_rooms[0]["project_id"] == pid and owner_rooms[0]["member_count"] == 2
    room_id = owner_rooms[0]["id"]

    sent = client.post(f"/messages/groups/{room_id}", headers=owner, json={"content": "welcome"}).json()
    assert sent["is_outgoing"] is True
    thread = client.get(f"/messages/groups/{room_id}", headers=guest).json()
    assert thread[-1]["content"] == "welcome" and thread[-1]["is_outgoing"] is False

    assert client.post("/messages/groups", headers=owner, json={"title": "Nope", "member_ids": [guest_id]}).status_code == 400
    conn = client.post("/connections/request", headers=owner, json={"target_user_id": guest_id}).json()
    assert client.put(f"/connections/{conn['id']}/accept", headers=guest).status_code == 200
    made = client.post("/messages/groups", headers=owner, json={"title": "Study group", "member_ids": [guest_id]}).json()
    assert made["project_id"] is None and made["title"] == "Study group"
    assert client.post("/messages/groups", headers=owner, json={"title": "Nope", "member_ids": [stranger_id]}).status_code == 400


def _connect(client, a, b, b_id):
    conn = client.post("/connections/request", headers=a, json={"target_user_id": b_id}).json()
    assert client.put(f"/connections/{conn['id']}/accept", headers=b).status_code == 200


def test_group_details_admin_photo_members_and_leaving(client, make_user):
    admin, admin_id = make_user()
    friend, friend_id = make_user()
    other, other_id = make_user()
    _connect(client, admin, friend, friend_id)
    _connect(client, admin, other, other_id)
    room = client.post("/messages/groups", headers=admin, json={"title": "Crew", "member_ids": [friend_id]}).json()
    rid = room["id"]

    details = client.get(f"/messages/groups/{rid}/details", headers=friend).json()
    assert details["is_admin"] is False and len(details["members"]) == 2
    assert [m["is_admin"] for m in details["members"]] == [True, False]
    assert details["members"][0]["user_id"] == admin_id

    assert client.patch(f"/messages/groups/{rid}", headers=friend, json={"title": "Mine"}).status_code == 403
    assert client.patch(f"/messages/groups/{rid}", headers=admin, json={"title": "Crew 2"}).json()["title"] == "Crew 2"
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    assert client.post(f"/messages/groups/{rid}/photo", headers=friend, files={"file": ("a.png", png, "image/png")}).status_code == 403
    photo = client.post(f"/messages/groups/{rid}/photo", headers=admin, files={"file": ("a.png", png, "image/png")}).json()
    assert photo["avatar_url"].startswith("/storage/avatars/room_")
    assert client.get("/messages/groups", headers=friend).json()[0]["avatar_url"] == photo["avatar_url"]

    added = client.post(f"/messages/groups/{rid}/members", headers=admin, json={"member_ids": [other_id]}).json()
    assert len(added["members"]) == 3
    assert len(client.delete(f"/messages/groups/{rid}/members/{other_id}", headers=admin).json()["members"]) == 2
    assert client.get(f"/messages/groups/{rid}/details", headers=other).status_code == 404

    assert client.post(f"/messages/groups/{rid}/leave", headers=admin).status_code == 200
    after = client.get(f"/messages/groups/{rid}/details", headers=friend).json()
    assert after["is_admin"] is True and len(after["members"]) == 1
    assert client.post(f"/messages/groups/{rid}/leave", headers=friend).status_code == 200
    assert client.get("/messages/groups", headers=friend).json() == []


def test_project_team_chat_follows_the_team_and_counts_unread(client, make_user):
    owner, _ = make_user()
    guest, _ = make_user()
    pid = _project(client, owner)
    name = client.get("/auth/me", headers=guest).json()["username"]
    inv = client.post(f"/collaborations/projects/{pid}/invitations", headers=owner, json={"username": name}).json()
    client.put(f"/collaborations/invitations/{inv['id']}/accept", headers=guest)
    rid = client.get("/messages/groups", headers=owner).json()[0]["id"]

    details = client.get(f"/messages/groups/{rid}/details", headers=guest).json()
    assert details["project_title"] == "Team" and details["is_admin"] is False
    assert {m["team_role"] for m in details["members"]} >= {"Owner"}
    assert client.post(f"/messages/groups/{rid}/leave", headers=guest).status_code == 400
    assert client.patch(f"/messages/groups/{rid}", headers=owner, json={"title": "x"}).status_code == 400

    client.post(f"/messages/groups/{rid}", headers=owner, json={"content": "one"})
    client.post(f"/messages/groups/{rid}", headers=owner, json={"content": "two"})
    counts = client.get("/messages/unread-count", headers=guest).json()
    assert counts["groups"] == 2 and counts["unread_count"] == 2
    client.get(f"/messages/groups/{rid}", headers=guest)
    assert client.get("/messages/unread-count", headers=guest).json()["unread_count"] == 0

    client.post(f"/messages/groups/{rid}", headers=owner, json={"content": "three"})
    assert client.get("/messages/unread-count", headers=guest).json()["groups"] == 1
    assert client.put(f"/messages/groups/{rid}/read", headers=guest).status_code == 200
    assert client.get("/messages/unread-count", headers=guest).json()["groups"] == 0
