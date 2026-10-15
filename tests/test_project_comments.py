"""Project comments: who may comment and delete, the owner's switch, pagination, and counts on every surface."""


def _project(client, headers, visibility="Public"):
    r = client.post("/projects", headers=headers, json={"title": "T", "category": "Gaming", "visibility": visibility})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_comment_rules_and_counts(client, make_user):
    owner, _ = make_user()
    fan, _ = make_user()
    other, _ = make_user()
    pid = _project(client, owner)
    url = f"/projects/{pid}/comments"

    assert client.post(url, json={"body": "hi"}).status_code in (401, 403)
    assert client.post(url, headers=fan, json={"body": "   "}).status_code == 422
    first = client.post(url, headers=fan, json={"body": " Nice work "})
    assert first.status_code == 201 and first.json()["body"] == "Nice work" and first.json()["can_delete"] is True
    cid = first.json()["id"]

    assert [n["type"] for n in client.get("/notifications", headers=owner).json()].count("project_comment") == 1
    assert client.get(f"/projects/{pid}", headers=other).json()["comments_count"] == 1
    listed = {p["id"]: p for p in client.get("/projects", headers=other).json()}
    assert listed[pid]["comments_count"] == 1
    feed_project = next(a["project"] for a in client.get("/network/feed", headers=fan).json()
                        if a["project"] and a["project"]["id"] == pid)
    assert feed_project["comments_count"] == 1

    page = client.get(url, headers=other).json()
    assert page["items"][0]["can_delete"] is False and page["next_page"] is None
    assert client.delete(f"{url}/{cid}", headers=other).status_code == 403
    assert client.get(url, headers=owner).json()["items"][0]["can_delete"] is True
    assert client.delete(f"{url}/{cid}", headers=owner).status_code == 204
    assert client.get(f"/projects/{pid}").json()["comments_count"] == 0


def test_owner_can_turn_comments_off(client, make_user):
    owner, _ = make_user()
    fan, _ = make_user()
    pid = _project(client, owner)
    body = {"title": "T", "category": "Gaming", "visibility": "Public", "comments_enabled": False}
    assert client.put(f"/projects/{pid}", headers=owner, json=body).json()["comments_enabled"] is False
    assert client.post(f"/projects/{pid}/comments", headers=fan, json={"body": "hello"}).status_code == 403
    del body["comments_enabled"]  # an edit that leaves the switch out keeps it as it was
    assert client.put(f"/projects/{pid}", headers=owner, json=body).json()["comments_enabled"] is False


def test_private_project_comments_hidden_and_pagination(client, make_user):
    owner, _ = make_user()
    stranger, _ = make_user()
    private_id = _project(client, owner, "Private")
    assert client.get(f"/projects/{private_id}/comments", headers=stranger).status_code == 404
    assert client.post(f"/projects/{private_id}/comments", headers=stranger, json={"body": "x"}).status_code == 404

    for i in range(5):
        assert client.post(f"/projects/{private_id}/comments", headers=owner, json={"body": f"c{i}"}).status_code == 201
    first = client.get(f"/projects/{private_id}/comments?limit=3", headers=owner).json()
    assert [c["body"] for c in first["items"]] == ["c4", "c3", "c2"] and first["next_page"]
    rest = client.get(f"/projects/{private_id}/comments", headers=owner,
                      params={"limit": 3, "page": first["next_page"]}).json()
    assert [c["body"] for c in rest["items"]] == ["c1", "c0"] and rest["next_page"] is None
    assert client.get(f"/projects/{private_id}/comments?page=bad", headers=owner).status_code == 400
