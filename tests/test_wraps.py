"""Monthly Wrap: real counts, self-actions excluded, snapshots stored once by the job, opt-in share."""

from datetime import date

from sqlalchemy import text

from app.services.wrap import current_month, shift_month


def _project(client, headers):
    return client.post("/projects", headers=headers, json={"title": "W", "category": "Gaming", "visibility": "Public"}).json()["id"]


def test_current_month_counts_real_activity(client, make_user):
    me, _ = make_user()
    fan, _ = make_user()
    empty = client.get("/wraps/current", headers=me).json()
    assert empty["status"] == "in_progress" and empty["empty"] and empty["persona"] is None
    assert set(empty["metrics"]) == {
        "projects_created", "top_tech", "project_likes_received", "project_comments_received", "posts_published",
        "post_likes_received", "post_comments_received", "connections_made", "projects_joined",
        "members_joined_my_projects", "invitations_sent", "top_liked_category"}

    pid = _project(client, me)
    client.post(f"/projects/{pid}/like", headers=me)  # self-like is not "received"
    client.post(f"/projects/{pid}/like", headers=fan)
    client.post(f"/projects/{pid}/comments", headers=fan, json={"body": "nice"})
    client.post("/network/posts", headers=me, json={"content": "hello"})

    m = client.get("/wraps/current", headers=me).json()
    assert m["metrics"]["projects_created"] == 1 and m["metrics"]["project_likes_received"] == 1
    assert m["metrics"]["project_comments_received"] == 1 and m["metrics"]["posts_published"] == 1
    assert m["metrics"]["top_liked_category"] == "Gaming" and m["persona"]["label"] == "The Explorer"
    assert client.get("/wraps/current", headers=fan).json()["metrics"]["top_liked_category"] == "Gaming"


def test_snapshot_job_is_idempotent_and_share_is_once(client, make_user):
    from app.db.session import SessionLocal, engine
    from app.scripts.generate_wraps import generate

    me, my_id = make_user()
    last = shift_month(current_month("America/Toronto"), -1)
    label = last.strftime("%Y-%m")
    pid = _project(client, me)
    with engine.begin() as conn:
        mid = f"{label}-15 12:00:00+00"
        conn.execute(text("UPDATE users SET created_at = :t WHERE id = :id"), {"t": f"{label}-01 12:00:00+00", "id": my_id})
        conn.execute(text("UPDATE projects SET created_at = :t WHERE id = :id"), {"t": mid, "id": pid})

    r = client.get(f"/wraps/{label}", headers=me)
    assert r.status_code == 404 and r.json()["code"] == "not_ready"
    assert client.get(f"/wraps/{current_month('America/Toronto').strftime('%Y-%m')}", headers=me).json()["code"] == "not_finished"

    with SessionLocal() as db:
        assert generate(db, user_id=my_id) == 1
        assert generate(db, user_id=my_id) == 0
        assert generate(db, month=date(2000, 1, 1), user_id=my_id) == 0  # before the account existed

    wrap = client.get(f"/wraps/{label}", headers=me).json()
    assert wrap["status"] == "completed" and wrap["metrics"]["projects_created"] == 1 and not wrap["shared"]
    assert client.get("/wraps", headers=me).json()["items"][0]["month"] == label

    first = client.post(f"/wraps/{label}/share", headers=me).json()
    again = client.post(f"/wraps/{label}/share", headers=me).json()
    assert first["created"] and not again["created"] and first["activity_id"] == again["activity_id"]
    assert client.get(f"/wraps/{label}", headers=me).json()["shared"]


def test_timezone_is_validated(client, make_user):
    me, _ = make_user()
    assert client.put("/profiles/me", headers=me, json={"timezone": "Mars/Base"}).status_code == 422
    assert client.put("/profiles/me", headers=me, json={"timezone": "Asia/Kolkata"}).json()["timezone"] == "Asia/Kolkata"
    assert client.get("/wraps/current", headers=me).json()["period"]["timezone"] == "Asia/Kolkata"
