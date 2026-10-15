"""Social rules: privacy, request states, like race, validation. Needs TEST_DATABASE_URL (see conftest)."""

from concurrent.futures import ThreadPoolExecutor


def _project(client, headers, visibility="Public"):
    r = client.post("/projects", headers=headers, json={"title": "T", "category": "Gaming", "visibility": visibility})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_private_projects_hidden_from_feed_and_collaborators(client, make_user):
    owner, _ = make_user()
    other, _ = make_user()
    private_id = _project(client, owner, "Private")
    public_id = _project(client, owner, "Public")

    def feed_ids(headers=None):
        return {a["project"]["id"] for a in client.get("/network/feed", headers=headers).json() if a["project"]}

    assert private_id not in feed_ids(other) and private_id not in feed_ids()
    assert public_id in feed_ids(other)
    assert private_id in feed_ids(owner)
    assert client.get(f"/collaborations/projects/{private_id}/collaborators", headers=other).status_code == 404
    assert client.post(f"/collaborations/projects/{private_id}/requests", headers=other, json={}).status_code == 404


def test_public_profile_hides_email(client, make_user):
    me, my_id = make_user()
    other, _ = make_user()
    assert client.get(f"/profiles/{my_id}", headers=other).json()["email"] is None
    assert client.get("/profiles/me", headers=me).json()["email"].endswith("@example.com")


def test_profile_update_clears_and_validates(client, make_user):
    me, _ = make_user()
    assert client.put("/profiles/me", headers=me, json={"github_url": "javascript:alert(1)"}).status_code == 422
    assert client.put("/profiles/me", headers=me, json={"github_url": "https://github.com/x", "headline": "Hi"}).json()["github_url"] == "https://github.com/x"
    body = client.put("/profiles/me", headers=me, json={"github_url": ""}).json()
    assert body["github_url"] is None and body["headline"] == "Hi"


def test_post_rules(client, make_user):
    me, _ = make_user()
    other, _ = make_user()
    other_project = _project(client, other)
    assert client.post("/network/posts", headers=me, json={"content": "x", "activity_type": "collaboration"}).status_code == 422
    assert client.post("/network/posts", headers=me, json={"content": "x", "project_id": other_project}).status_code == 403
    assert client.post("/network/posts", headers=me, json={"content": "x", "title": "t" * 300}).status_code == 422
    assert client.post("/network/posts", headers=me, json={"content": "hello"}).status_code == 200


def test_connection_accept_only_when_pending(client, make_user):
    a, _ = make_user()
    b, b_id = make_user()
    conn_id = client.post("/connections/request", headers=a, json={"target_user_id": b_id}).json()["id"]
    assert client.put(f"/connections/{conn_id}/decline", headers=b).status_code == 200
    assert client.put(f"/connections/{conn_id}/accept", headers=b).status_code == 400


def test_like_double_click_is_safe_and_notifies_once(client, make_user):
    author, _ = make_user()
    fan, _ = make_user()
    act_id = client.post("/network/posts", headers=author, json={"content": "like me"}).json()["id"]

    like = lambda: client.post(f"/network/activities/{act_id}/like", headers=fan)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: like(), range(2)))
    assert all(r.status_code == 200 for r in results)

    for _ in range(3):  # toggling again never errors and the count always matches the state
        body = like().json()
        assert body["likes_count"] == (1 if body["is_liked"] else 0)
    likes = [n for n in client.get("/notifications", headers=author).json() if n["type"] == "like"]
    assert len(likes) == 1


def test_duplicate_username_and_case_insensitive_email(client, make_user):
    payload = {"username": "dupname", "email": "Dup@Example.com", "password": "password123"}
    assert client.post("/auth/register", json=payload).status_code == 200
    assert client.post("/auth/register", json={**payload, "email": "x@example.com"}).status_code == 400
    assert client.post("/auth/register", json={**payload, "username": "other1", "email": "dup@example.com"}).status_code == 400
    assert client.post("/auth/login", data={"username": "DUP@example.com", "password": "password123"}).status_code == 200


def test_conversations_and_pagination(client, make_user):
    a, a_id = make_user()
    b, b_id = make_user()
    for i in range(3):
        assert client.post("/messages", headers=a, json={"receiver_id": b_id, "content": f"m{i}"}).status_code == 200
    convs = client.get("/messages/conversations", headers=b).json()
    assert len(convs) == 1 and convs[0]["unread_count"] == 3 and convs[0]["last_message"]["content"] == "m2"
    thread = client.get(f"/messages/conversations/{a_id}?limit=2", headers=b).json()
    assert [m["content"] for m in thread] == ["m1", "m2"]
    older = client.get(f"/messages/conversations/{a_id}", headers=b, params={"before": thread[0]["created_at"]}).json()
    assert [m["content"] for m in older] == ["m0"]
