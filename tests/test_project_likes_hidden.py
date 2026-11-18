"""Per-project hide like count and comments switch on create."""


def test_likes_hidden_and_comments_on_create(client, make_user):
    owner, _ = make_user()
    fan, _ = make_user()

    created = client.post("/projects", headers=owner, json={
        "title": "Quiet",
        "category": "Gaming",
        "visibility": "Public",
        "comments_enabled": False,
        "likes_hidden": True,
    })
    assert created.status_code == 200, created.text
    pid = created.json()["id"]
    assert created.json()["comments_enabled"] is False
    assert created.json()["likes_hidden"] is True

    assert client.post(f"/projects/{pid}/comments", headers=fan, json={"body": "nice"}).status_code == 403

    liked = client.post(f"/projects/{pid}/like", headers=fan)
    assert liked.status_code == 200
    assert liked.json()["is_liked"] is True
    assert liked.json()["likes_hidden"] is True
    assert liked.json()["likes_count"] == 0  # count hidden from non-owners

    fan_view = client.get(f"/projects/{pid}", headers=fan).json()
    assert fan_view["likes_hidden"] is True and fan_view["likes_count"] == 0 and fan_view["is_liked"] is True

    owner_view = client.get(f"/projects/{pid}", headers=owner).json()
    assert owner_view["likes_hidden"] is True and owner_view["likes_count"] == 1

    body = {"title": "Quiet", "category": "Gaming", "visibility": "Public", "likes_hidden": False, "comments_enabled": True}
    assert client.put(f"/projects/{pid}", headers=owner, json=body).json()["likes_hidden"] is False
    assert client.get(f"/projects/{pid}", headers=fan).json()["likes_count"] == 1


def test_octet_stream_mp4_upload_allowed(client, make_user):
    me, _ = make_user()
    # ftyp box at offset 4 — valid enough for the chat sniff
    mp4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 40
    res = client.post(
        "/messages/attachments",
        headers=me,
        files={"file": ("clip.mp4", mp4, "application/octet-stream")},
    )
    assert res.status_code == 200, res.text
    assert res.json()["media_kind"] == "video"
