"""Feed: post types, visibility on every activity route, paging, comment previews, edit/delete, rate limits."""


def _post(client, headers, **body):
    return client.post("/network/posts", headers=headers, json={"content": "hi", **body})


def test_post_types_edit_delete_and_moderation(client, make_user):
    me, _ = make_user()
    other, _ = make_user()
    for kind in ("post", "milestone", "collab_call", "project_update"):
        assert _post(client, me, activity_type=kind).status_code == 200
    assert _post(client, me, activity_type="wrap_up_published").status_code == 422

    act = _post(client, me, content="first draft").json()
    assert act["can_edit"] and act["activity_type"] == "post"
    assert client.patch(f"/network/activities/{act['id']}", headers=other, json={"content": "x"}).status_code == 403
    edited = client.patch(f"/network/activities/{act['id']}", headers=me, json={"content": " final "}).json()
    assert edited["content"] == "final"

    comment = client.post(f"/network/activities/{act['id']}/comments", headers=other, json={"comment_text": "rude"}).json()
    assert comment["can_delete"]
    assert client.delete(f"/network/comments/{comment['id']}", headers=me).status_code == 200  # post author moderates

    assert client.delete(f"/network/activities/{act['id']}", headers=other).status_code == 403
    assert client.delete(f"/network/activities/{act['id']}", headers=me).status_code == 200
    assert client.get(f"/network/activities/{act['id']}", headers=me).status_code == 404


def test_previews_paging_and_counts(client, make_user):
    me, my_id = make_user()
    fans = [make_user()[0] for _ in range(3)]
    act = _post(client, me, content="popular").json()
    for i, fan in enumerate(fans):
        client.post(f"/network/activities/{act['id']}/comments", headers=fan, json={"comment_text": f"c{i}"})

    item = client.get(f"/network/activities/{act['id']}", headers=me).json()
    assert item["comments_count"] == 3 and [c["comment_text"] for c in item["comments"]] == ["c1", "c2"]
    page = client.get(f"/network/activities/{act['id']}/comments?limit=2", headers=me).json()
    rest = client.get(f"/network/activities/{act['id']}/comments?limit=2&page={page['next_page']}", headers=me).json()
    assert [c["comment_text"] for c in page["items"] + rest["items"]] == ["c2", "c1", "c0"]

    _post(client, me, content="second")
    first = client.get(f"/network/feed?user_id={my_id}&limit=1").json()
    second = client.get(f"/network/feed?user_id={my_id}&limit=1&page={first['next_page']}").json()
    assert first["items"][0]["content"] == "second" and second["items"][0]["content"] == "popular"


def test_private_project_posts_are_hidden_everywhere(client, make_user):
    owner, _ = make_user()
    stranger, _ = make_user()
    pid = client.post("/projects", headers=owner, json={"title": "Secret", "category": "Gaming", "visibility": "Private"}).json()["id"]
    act = _post(client, owner, project_id=pid, activity_type="project_update").json()
    assert client.get(f"/network/activities/{act['id']}", headers=stranger).status_code == 404
    assert client.post(f"/network/activities/{act['id']}/like", headers=stranger).status_code == 404
    assert client.post(f"/network/activities/{act['id']}/comments", headers=stranger, json={"comment_text": "x"}).status_code == 404
    assert client.get(f"/network/activities/{act['id']}/comments", headers=stranger).status_code == 404


def test_posting_is_rate_limited(client, make_user):
    me, _ = make_user()
    codes = [_post(client, me).status_code for _ in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429
