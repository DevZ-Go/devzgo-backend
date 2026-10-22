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
