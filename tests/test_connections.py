"""Connection lifecycle: one row per pair, explicit answers, quiet decline cooldown, withdraw/remove."""

from datetime import timedelta

from sqlalchemy import text


def _request(client, headers, target):
    return client.post("/connections/request", headers=headers, json={"target_user_id": target})


def _status(client, headers, other):
    return client.get(f"/connections/status/{other}", headers=headers).json()["status"]


def test_reverse_request_points_at_the_incoming_one(client, make_user):
    a, a_id = make_user()
    b, b_id = make_user()
    conn_id = _request(client, a, b_id).json()["id"]
    assert _request(client, a, b_id).json()["id"] == conn_id  # repeat is a no-op
    r = _request(client, b, a_id)
    assert r.status_code == 409 and r.json()["code"] == "incoming_request_exists" and r.json()["connection_id"] == conn_id

    incoming = client.get("/connections/requests?direction=incoming", headers=b).json()["items"]
    assert [c["id"] for c in incoming] == [conn_id]
    assert client.put(f"/connections/{conn_id}/accept", headers=b).json()["accepted_at"]
    assert _request(client, a, b_id).status_code == 409
    assert _status(client, a, b_id) == "accepted"


def test_decline_is_quiet_and_has_a_cooldown(client, make_user):
    from app.db.session import engine

    a, a_id = make_user()
    b, b_id = make_user()
    conn_id = _request(client, a, b_id).json()["id"]
    client.put(f"/connections/{conn_id}/decline", headers=b)

    assert _status(client, a, b_id) == "pending_sent" and _status(client, b, a_id) is None
    assert _request(client, a, b_id).json()["status"] == "pending"
    assert client.delete(f"/connections/{conn_id}", headers=a).status_code == 200  # withdrawing keeps the cooldown
    assert _status(client, a, b_id) == "pending_sent"
    before = len(client.get("/notifications", headers=b).json())

    with engine.begin() as conn:
        conn.execute(text("UPDATE connections SET updated_at = now() - :age WHERE id = :id"),
                     {"age": timedelta(days=8), "id": conn_id})
    assert _status(client, a, b_id) is None
    assert _request(client, a, b_id).json()["status"] == "pending"
    assert len(client.get("/notifications", headers=b).json()) == before + 1


def test_list_search_mutuals_and_remove(client, make_user):
    me, me_id = make_user()
    friend, friend_id = make_user()
    other, other_id = make_user()
    for headers, target, accepter in ((me, friend_id, friend), (me, other_id, other), (friend, other_id, other)):
        cid = _request(client, headers, target).json()["id"]
        client.put(f"/connections/{cid}/accept", headers=accepter)

    items = client.get("/connections", headers=me).json()["items"]
    assert {c["partner"]["id"] for c in items} == {friend_id, other_id}
    assert all(c["mutual_count"] == 1 for c in items)
    friend_name = next(c["partner"]["username"] for c in items if c["partner"]["id"] == friend_id)
    found = client.get(f"/connections?search={friend_name}", headers=me).json()["items"]
    assert [c["partner"]["id"] for c in found] == [friend_id]

    page = client.get("/connections?limit=1", headers=me).json()
    rest = client.get(f"/connections?limit=1&page={page['next_page']}", headers=me).json()
    assert {page["items"][0]["id"], rest["items"][0]["id"]} == {c["id"] for c in items}

    removed = next(c["id"] for c in items if c["partner"]["id"] == friend_id)
    assert client.delete(f"/connections/{removed}", headers=me).status_code == 200
    assert _status(client, friend, me_id) is None
    assert client.get(f"/profiles/{friend_id}", headers=me).json()["connection_status"] is None


def test_receiver_cannot_withdraw_and_suggestions_skip_known_people(client, make_user):
    a, a_id = make_user()
    b, b_id = make_user()
    conn_id = _request(client, a, b_id).json()["id"]
    assert client.delete(f"/connections/{conn_id}", headers=b).status_code == 400
    suggested = {u["id"] for u in client.get("/connections/suggested?limit=20", headers=a).json()}
    assert b_id not in suggested and a_id not in suggested
