"""Profile privileges: who can see work, message, comment, and be found."""


def _project(client, headers):
    return client.post("/projects", headers=headers, json={
        "title": "Quiet", "category": "Gaming", "visibility": "Public",
    }).json()["id"]


def test_connections_only_profile_opens_after_a_connection(client, make_user):
    owner, owner_id = make_user()
    stranger, _ = make_user()
    pid = _project(client, owner)
    assert client.put("/profiles/me", headers=owner, json={"comment_policy": "connections"}).status_code == 200
    assert client.post(f"/projects/{pid}/comments", headers=stranger, json={"body": "hey"}).status_code == 403
    saved = client.put("/profiles/me", headers=owner, json={
        "profile_visibility": "connections",
        "message_policy": "connections",
        "comment_policy": "connections",
        "activity_visibility": "connections",
        "discoverable": False,
    })
    assert saved.status_code == 200
    assert pid not in [p["id"] for p in client.get("/projects", headers=stranger).json()]
    assert client.get(f"/projects/{pid}", headers=stranger).status_code == 403
    seen = client.get(f"/profiles/{owner_id}", headers=stranger).json()
    assert seen["restricted"] is True and seen["can_message"] is False and seen["bio"] is None
    assert client.post("/messages", headers=stranger, json={"receiver_id": owner_id, "content": "hi"}).status_code == 403
    username = client.get("/auth/me", headers=owner).json()["username"]
    assert client.get("/profiles/search", headers=stranger, params={"query": username}).json() == []
    feed = client.get("/network/feed", headers=stranger).json()["items"]
    assert owner_id not in [item["user_id"] for item in feed]

    assert client.post("/connections/request", headers=stranger, json={"target_user_id": owner_id}).status_code == 200
    incoming = client.get("/connections/requests", headers=owner).json()["items"]
    assert client.put(f"/connections/{incoming[0]['id']}/accept", headers=owner).status_code == 200
    assert client.get(f"/projects/{pid}", headers=stranger).status_code == 200
    assert client.get(f"/profiles/{owner_id}", headers=stranger).json()["restricted"] is False
    assert client.post("/messages", headers=stranger, json={"receiver_id": owner_id, "content": "hi"}).status_code == 200


def test_closed_connection_requests_and_share_milestone(client, make_user):
    author, author_id = make_user()
    assert client.put("/profiles/me", headers=author, json={"allow_connection_requests": False}).status_code == 200
    stranger, _ = make_user()
    assert client.post("/connections/request", headers=stranger, json={"target_user_id": author_id}).status_code == 403

    post = client.post("/network/posts", headers=author, json={"content": "a small post"}).json()
    fans = [stranger] + [make_user()[0] for _ in range(4)]
    assert client.post(f"/network/activities/{post['id']}/share", headers=fans[0]).status_code == 200
    for headers in fans:
        assert client.post(f"/network/activities/{post['id']}/like", headers=headers).status_code == 200
    kinds = {n["type"] for n in client.get("/notifications", headers=author).json()}
    assert {"share", "like", "achievement"} <= kinds
