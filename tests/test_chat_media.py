"""Chat photos, videos, and emoji reactions stay inside the conversation."""

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
WEBM = b"\x1a\x45\xdf\xa3" + b"0" * 32


def _upload(client, headers, name, body, content_type):
    return client.post("/messages/attachments", headers=headers, files={"file": (name, body, content_type)})


def test_photo_shows_the_sender_and_stays_private(client, make_user):
    sender, sender_id = make_user()
    friend, _friend_id = make_user()
    stranger, _stranger_id = make_user()
    username = client.get("/auth/me", headers=sender).json()["username"]

    stored = _upload(client, sender, "shot.png", PNG, "image/png")
    assert stored.status_code == 200, stored.text
    key = stored.json()["media_key"]
    assert stored.json()["media_kind"] == "image"
    friend_id = client.get("/auth/me", headers=friend).json()["id"]
    assert client.post("/messages", headers=friend, json={"receiver_id": sender_id, "media_key": key}).status_code == 400
    assert _upload(client, sender, "page.svg", b"<svg></svg>", "image/svg+xml").status_code == 400
    assert _upload(client, sender, "shot.png", b"<html></html>", "image/png").status_code == 400

    sent = client.post("/messages", headers=sender, json={
        "receiver_id": friend_id, "content": "look", "media_key": key,
    })
    assert sent.status_code == 200, sent.text
    body = sent.json()
    assert body["sender_id"] == sender_id
    assert body["sender_username"] == username
    assert body["sender_name"]
    assert body["media_kind"] == "image"
    assert body["media_url"].endswith(f"/attachments/{body['id']}")

    fetched = client.get(body["media_url"], headers=friend)
    assert fetched.status_code == 200
    assert fetched.headers["content-type"].startswith("image/png")
    assert fetched.headers["x-content-type-options"] == "nosniff"
    assert fetched.content.startswith(b"\x89PNG")
    assert client.get(body["media_url"], headers=stranger).status_code == 404

    thread = client.get(f"/messages/conversations/{sender_id}", headers=friend).json()
    assert thread[-1]["sender_username"] == username
    assert thread[-1]["media_kind"] == "image"

    added = client.post(f"/messages/{body['id']}/reactions", headers=friend, json={"emoji": "👍"})
    assert added.status_code == 200
    assert added.json()["reactions"] == [{"emoji": "👍", "count": 1, "mine": True}]
    heart = client.post(f"/messages/{body['id']}/reactions", headers=friend, json={"emoji": "❤"}).json()["reactions"]
    assert {row["emoji"] for row in heart} == {"👍", "❤️"}
    sender_view = client.post(f"/messages/{body['id']}/reactions", headers=sender, json={"emoji": "👍"}).json()["reactions"]
    assert {row["emoji"]: row["count"] for row in sender_view}["👍"] == 2
    removed = client.post(f"/messages/{body['id']}/reactions", headers=friend, json={"emoji": "👍"}).json()["reactions"]
    assert {row["emoji"]: row["count"] for row in removed}["👍"] == 1
    assert client.post(f"/messages/{body['id']}/reactions", headers=stranger, json={"emoji": "🔥"}).status_code == 404
    assert client.post(f"/messages/{body['id']}/reactions", headers=friend, json={"emoji": "💀"}).status_code == 200
    assert client.post(f"/messages/{body['id']}/reactions", headers=friend, json={"emoji": "nope"}).status_code == 400


def test_group_video_names_the_sender(client, make_user):
    owner, _ = make_user()
    guest, guest_id = make_user()
    outsider, _ = make_user()
    conn = client.post("/connections/request", headers=owner, json={"target_user_id": guest_id}).json()
    assert client.put(f"/connections/{conn['id']}/accept", headers=guest).status_code == 200
    room_id = client.post("/messages/groups", headers=owner, json={"title": "Clips", "member_ids": [guest_id]}).json()["id"]
    stored = _upload(client, owner, "clip.webm", WEBM, "video/webm")
    assert stored.status_code == 200, stored.text
    sent = client.post(f"/messages/groups/{room_id}", headers=owner, json={"content": "", "media_key": stored.json()["media_key"]})
    assert sent.status_code == 200, sent.text
    assert sent.json()["media_kind"] == "video"
    assert sent.json()["sender_username"]
    video = client.get(sent.json()["media_url"], headers=guest)
    assert video.status_code == 200
    assert video.headers["content-type"].startswith("video/webm")
    assert client.get(sent.json()["media_url"], headers=outsider).status_code == 404
    reacted = client.post(f"/messages/groups/{room_id}/{sent.json()['id']}/reactions", headers=guest, json={"emoji": "🎉"})
    assert reacted.status_code == 200
    assert reacted.json()["reactions"][0]["emoji"] == "🎉"
    listed = client.get("/messages/groups", headers=guest).json()
    assert listed[0]["last_message"] == "Video"


def test_unsend_and_view_once(client, make_user):
    sender, sender_id = make_user()
    friend, _ = make_user()
    friend_id = client.get("/auth/me", headers=friend).json()["id"]

    sent = client.post("/messages", headers=sender, json={
        "receiver_id": friend_id, "content": "secret note", "view_once": True,
    })
    assert sent.status_code == 200, sent.text
    message_id = sent.json()["id"]
    assert sent.json()["view_once"] is True and sent.json()["content"] == "secret note"

    sealed = client.get(f"/messages/conversations/{sender_id}", headers=friend).json()[-1]
    assert sealed["content"] == "" and sealed["opened"] is False and sealed["view_once"] is True

    opened = client.post(f"/messages/{message_id}/open", headers=friend)
    assert opened.status_code == 200
    assert opened.json()["content"] == "secret note"
    assert client.post(f"/messages/{message_id}/open", headers=friend).json()["content"] == ""
    assert client.post(f"/messages/{message_id}/consume", headers=friend).json()["opened"] is True
    assert client.get(f"/messages/conversations/{friend_id}", headers=sender).json()[-1]["content"] == ""

    assert client.post(f"/messages/{message_id}/hide", headers=sender).status_code == 200
    sender_thread = client.get(f"/messages/conversations/{friend_id}", headers=sender).json()
    friend_thread = client.get(f"/messages/conversations/{sender_id}", headers=friend).json()
    assert all(row["id"] != message_id for row in sender_thread)
    assert any(row["id"] == message_id for row in friend_thread)

    plain = client.post("/messages", headers=sender, json={"receiver_id": friend_id, "content": "hello"})
    assert client.post(f"/messages/{plain.json()['id']}/unsend", headers=friend).status_code == 403
    gone = client.post(f"/messages/{plain.json()['id']}/unsend", headers=sender)
    assert gone.status_code == 200
    assert gone.json()["unsent"] is True and gone.json()["content"] == ""
    plain_id = plain.json()["id"]
    assert all(row["id"] != plain_id for row in client.get(f"/messages/conversations/{sender_id}", headers=friend).json())
    assert all(row["id"] != plain_id for row in client.get(f"/messages/conversations/{friend_id}", headers=sender).json())

    photo = _upload(client, sender, "shot.png", PNG, "image/png")
    assert photo.status_code == 200, photo.text
    sent_photo = client.post("/messages", headers=sender, json={
        "receiver_id": friend_id, "content": "", "media_key": photo.json()["media_key"],
    })
    assert sent_photo.status_code == 200, sent_photo.text
    photo_id = sent_photo.json()["id"]
    removed = client.post(f"/messages/{photo_id}/unsend", headers=sender)
    assert removed.status_code == 200
    assert removed.json()["unsent"] is True and removed.json()["media_url"] is None
    assert client.get(f"/messages/attachments/{photo_id}", headers=friend).status_code == 404
    assert all(row["id"] != photo_id for row in client.get(f"/messages/conversations/{sender_id}", headers=friend).json())
