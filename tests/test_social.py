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


def test_project_likes(client, make_user):
    owner, _ = make_user()
    fan, _ = make_user()
    public_id = _project(client, owner)
    private_id = _project(client, owner, "Private")

    assert client.post(f"/projects/{private_id}/like", headers=fan).status_code == 404
    for _ in range(2):  # liking twice stays at one like
        body = client.post(f"/projects/{public_id}/like", headers=fan).json()
        assert body == {"project_id": public_id, "likes_count": 1, "is_liked": True}
    detail = client.get(f"/projects/{public_id}", headers=fan).json()
    assert detail["likes_count"] == 1 and detail["is_liked"] is True
    listed = {p["id"]: p for p in client.get("/projects", headers=fan).json()}
    assert listed[public_id]["likes_count"] == 1
    assert len([n for n in client.get("/notifications", headers=owner).json() if n["type"] == "project_like"]) == 1
    assert client.delete(f"/projects/{public_id}/like", headers=fan).json()["likes_count"] == 0
    assert client.delete(f"/projects/{public_id}", headers=owner).status_code == 204


def test_websocket_rejects_bad_token(client):
    import pytest
    from starlette.websockets import WebSocketDisconnect

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"token": "not-a-jwt"})
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4401


def test_websocket_delivers_message_and_notification(client, make_user):
    a, a_id = make_user()
    b, b_id = make_user()
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"token": b["Authorization"].split()[1]})
        assert ws.receive_json() == {"type": "ready"}
        assert client.post("/messages", headers=a, json={"receiver_id": b_id, "content": "live?"}).status_code == 200
        events = {e["type"]: e for e in (ws.receive_json(), ws.receive_json())}
        assert events["message.new"]["message"]["content"] == "live?"
        assert events["message.new"]["message"]["is_outgoing"] is False
        assert events["notification.new"]["notification_type"] == "new_message"


def test_password_reset_flow(client, make_user, monkeypatch):
    import app.routes.auth as auth_routes

    sent = []
    monkeypatch.setattr(auth_routes, "send_email", lambda to, subject, html, note="": sent.append(note))
    headers, _ = make_user()
    email = client.get("/auth/me", headers=headers).json()["email"]

    unknown = client.post("/auth/forgot-password", json={"email": "nobody@example.com"})
    known = client.post("/auth/forgot-password", json={"email": email.upper()})
    assert unknown.status_code == known.status_code == 200 and unknown.json() == known.json()
    assert len(sent) == 1
    client.post("/auth/forgot-password", json={"email": email})
    assert len(sent) == 1  # second request inside a minute is ignored

    token = sent[0].split("token=")[1]
    assert client.post("/auth/reset-password", json={"token": "x" * 20, "new_password": "newpassword1"}).status_code == 400
    assert client.post("/auth/reset-password", json={"token": token, "new_password": "short"}).status_code == 422
    assert client.post("/auth/reset-password", json={"token": token, "new_password": "newpassword1"}).status_code == 200
    assert client.post("/auth/reset-password", json={"token": token, "new_password": "another123"}).status_code == 400
    assert client.post("/auth/login", data={"username": email, "password": "newpassword1"}).status_code == 200
    assert client.post("/auth/login", data={"username": email, "password": "password123"}).status_code == 401


def test_expired_reset_token_rejected(client, make_user):
    import hashlib
    from datetime import datetime, timedelta, timezone
    from app.db.session import SessionLocal
    from app.models.password_reset import PasswordResetToken

    _, user_id = make_user()
    with SessionLocal() as db:
        db.add(PasswordResetToken(user_id=user_id, token_hash=hashlib.sha256(b"expired-token-123").hexdigest(),
                                  expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
        db.commit()
    assert client.post("/auth/reset-password", json={"token": "expired-token-123", "new_password": "newpassword1"}).status_code == 400


def _github_login(client, monkeypatch, identity):
    import app.routes.github_auth as gh

    monkeypatch.setenv("GITHUB_CLIENT_ID", "cid")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "secret")
    monkeypatch.setattr(gh, "fetch_github_identity", lambda code: identity)
    start = client.get("/auth/github/login", follow_redirects=False)
    assert start.status_code == 307 and start.headers["location"].startswith("https://github.com/login/oauth/authorize")
    state = start.headers["location"].split("state=")[1].split("&")[0]
    done = client.get(f"/auth/github/callback?code=abc&state={state}", follow_redirects=False)
    return done.headers["location"]


def test_github_login_creates_then_reuses_account(client, monkeypatch):
    identity = {"id": "9001", "login": "octo cat", "name": "Octo", "avatar_url": "https://a/x.png",
                "html_url": "https://github.com/octocat", "email": "Octo@Example.com"}
    first = _github_login(client, monkeypatch, identity)
    assert "/auth/callback#token=" in first
    headers = {"Authorization": "Bearer " + first.split("#token=")[1]}
    me = client.get("/profiles/me", headers=headers).json()
    assert me["username"] == "octocat" and me["github_url"] == "https://github.com/octocat" and me["email"] == "octo@example.com"
    assert client.post("/auth/login", data={"username": "octo@example.com", "password": "anything1"}).status_code == 401

    again = _github_login(client, monkeypatch, {**identity, "email": None})
    assert client.get("/auth/me", headers={"Authorization": "Bearer " + again.split("#token=")[1]}).json()["id"] == me["user_id"]


def test_github_login_links_by_verified_email_and_rejects_bad_state(client, make_user, monkeypatch):
    headers, user_id = make_user()
    email = client.get("/auth/me", headers=headers).json()["email"]
    linked = _github_login(client, monkeypatch, {"id": "777", "login": "x", "name": None, "avatar_url": None,
                                                 "html_url": "https://github.com/x", "email": email})
    assert client.get("/auth/me", headers={"Authorization": "Bearer " + linked.split("#token=")[1]}).json()["id"] == user_id

    other = _github_login(client, monkeypatch, {"id": "778", "login": "y", "name": None, "avatar_url": None,
                                                "html_url": None, "email": email})
    assert other.endswith("/login?error=github_linked")
    assert client.get("/auth/github/callback?code=abc&state=forged", follow_redirects=False).headers["location"].endswith("error=github_state")
