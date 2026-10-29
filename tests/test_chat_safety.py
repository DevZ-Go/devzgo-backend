"""Block, restrict, mute, group admins, file attachments, and the recruiter's challenge replies."""

from datetime import datetime, timedelta, timezone


def _connect(client, a, b, b_id):
    conn = client.post("/connections/request", headers=a, json={"target_user_id": b_id})
    assert conn.status_code in (200, 201), conn.text
    assert client.put(f"/connections/{conn.json()['id']}/accept", headers=b).status_code == 200


def _notes(client, headers, kind="new_message"):
    return [n for n in client.get("/notifications", headers=headers).json() if n["type"] == kind]


def test_block_stops_messages_and_connections_both_ways(client, make_user):
    me, me_id = make_user()
    them, them_id = make_user()
    _connect(client, me, them, them_id)

    blocked = client.put(f"/safety/people/{them_id}", headers=me, json={"kind": "block"})
    assert blocked.status_code == 200 and blocked.json()["relation"] == "block"
    assert client.get(f"/safety/people/{me_id}", headers=them).json()["blocked_me"] is True

    assert client.post("/messages", headers=them, json={"receiver_id": me_id, "content": "hi"}).status_code == 403
    mine = client.post("/messages", headers=me, json={"receiver_id": them_id, "content": "hi"})
    assert mine.status_code == 403 and "Unblock" in mine.json()["detail"]
    assert client.post("/connections/request", headers=them, json={"target_user_id": me_id}).status_code == 403
    assert client.post("/messages/groups", headers=me, json={"title": "x", "member_ids": [them_id]}).status_code == 400

    overview = client.get("/safety", headers=me).json()
    assert [row["user"]["id"] for row in overview["blocked"]] == [them_id]

    assert client.delete(f"/safety/people/{them_id}", headers=me).json()["relation"] is None
    assert client.post("/connections/request", headers=them, json={"target_user_id": me_id}).status_code in (200, 201)


def test_restrict_keeps_chat_but_hides_reads_and_pings(client, make_user):
    me, me_id = make_user()
    them, them_id = make_user()
    username = client.get("/auth/me", headers=them).json()["username"]

    by_name = client.post("/safety/people", headers=me, json={"username": "@" + username, "kind": "restrict"})
    assert by_name.status_code == 200 and by_name.json()["relation"] == "restrict"

    assert client.post("/messages", headers=them, json={"receiver_id": me_id, "content": "hello"}).status_code == 200
    assert _notes(client, me) == []
    assert client.get("/messages/unread-count", headers=me).json()["unread_count"] == 0

    client.get(f"/messages/conversations/{them_id}", headers=me)
    their_view = client.get(f"/messages/conversations/{me_id}", headers=them).json()
    assert their_view[-1]["read"] is False
    convo = client.get("/messages/conversations", headers=me).json()[0]
    assert convo["relation"] == "restrict"


def test_mute_silences_a_chat_and_a_group(client, make_user):
    me, me_id = make_user()
    them, them_id = make_user()
    _connect(client, me, them, them_id)

    assert client.put(f"/safety/mutes/people/{them_id}", headers=me).json()["muted"] is True
    assert client.post("/messages", headers=them, json={"receiver_id": me_id, "content": "psst"}).status_code == 200
    assert _notes(client, me) == []
    assert client.get("/messages/unread-count", headers=me).json()["direct"] == 0
    assert client.get("/messages/conversations", headers=me).json()[0]["muted"] is True

    room_id = client.post("/messages/groups", headers=them, json={"title": "Team", "member_ids": [me_id]}).json()["id"]
    assert client.put(f"/safety/mutes/groups/{room_id}", headers=me).status_code == 200
    client.post(f"/messages/groups/{room_id}", headers=them, json={"content": "standup"})
    assert client.get("/messages/unread-count", headers=me).json()["groups"] == 0
    assert client.get("/messages/groups", headers=me).json()[0]["muted"] is True

    muted = client.get("/safety", headers=me).json()["muted"]
    assert {row["room_title"] or row["user"]["id"] for row in muted} == {"Team", them_id}
    assert client.delete(f"/safety/mutes/people/{them_id}", headers=me).json()["muted"] is False


def test_group_admin_can_promote_and_demote(client, make_user):
    owner, _ = make_user()
    helper, helper_id = make_user()
    guest, guest_id = make_user()
    _connect(client, owner, helper, helper_id)
    _connect(client, owner, guest, guest_id)
    room_id = client.post("/messages/groups", headers=owner, json={"title": "Crew", "member_ids": [helper_id, guest_id]}).json()["id"]

    assert client.post(f"/messages/groups/{room_id}/admins/{guest_id}", headers=helper).status_code == 403
    promoted = client.post(f"/messages/groups/{room_id}/admins/{helper_id}", headers=owner)
    assert promoted.status_code == 200
    row = next(m for m in promoted.json()["members"] if m["user_id"] == helper_id)
    assert row["is_admin"] is True and row["admin_fixed"] is False

    assert client.patch(f"/messages/groups/{room_id}", headers=helper, json={"title": "Crew 2"}).status_code == 200
    assert client.post(f"/messages/groups/{room_id}/admins/{guest_id}", headers=helper).status_code == 200
    assert client.delete(f"/messages/groups/{room_id}/admins/{guest_id}", headers=helper).status_code == 403
    assert client.delete(f"/messages/groups/{room_id}/members/{guest_id}", headers=helper).status_code == 403
    owner_id = client.get("/auth/me", headers=owner).json()["id"]
    assert client.delete(f"/messages/groups/{room_id}/admins/{owner_id}", headers=helper).status_code == 400

    demoted = client.delete(f"/messages/groups/{room_id}/admins/{guest_id}", headers=owner)
    assert next(m for m in demoted.json()["members"] if m["user_id"] == guest_id)["is_admin"] is False
    assert client.delete(f"/messages/groups/{room_id}/members/{guest_id}", headers=helper).status_code == 200


def test_files_are_downloads_with_their_name(client, make_user):
    me, _ = make_user()
    them, them_id = make_user()
    stored = client.post("/messages/attachments", headers=me,
                         files={"file": ("Résumé 2026.pdf", b"%PDF-1.7\n" + b"0" * 40, "application/pdf")})
    assert stored.status_code == 200, stored.text
    assert stored.json()["media_kind"] == "file"
    bad = client.post("/messages/attachments", headers=me, files={"file": ("x.pdf", b"<html>", "application/pdf")})
    assert bad.status_code == 400
    binary = client.post("/messages/attachments", headers=me, files={"file": ("notes.txt", b"a\x00b", "text/plain")})
    assert binary.status_code == 400
    assert client.post("/messages/attachments", headers=me, files={"file": ("run.exe", b"MZ", "application/x-msdownload")}).status_code == 400

    sent = client.post("/messages", headers=me, json={
        "receiver_id": them_id, "media_key": stored.json()["media_key"], "media_name": "../Résumé 2026.pdf",
    })
    assert sent.status_code == 200, sent.text
    body = sent.json()
    assert body["media_kind"] == "file" and body["media_name"] == "Rsum 2026.pdf"
    got = client.get(body["media_url"], headers=them)
    assert got.status_code == 200
    assert got.headers["content-disposition"].startswith("attachment;")
    assert got.headers["x-content-type-options"] == "nosniff"


def test_recruiter_hears_when_an_invitation_is_answered(client, make_user, make_recruiter):
    recruiter, _ = make_recruiter("Maple Labs")
    dev, dev_id = make_user()
    client.put("/profiles/me", headers=dev, json={"open_to_work": True})
    body = {
        "title": "Build a task API", "challenge_type": "backend", "technologies": ["FastAPI"],
        "difficulty": "intermediate", "estimated_minutes": 120, "description": "A small REST API.",
        "instructions": "Create endpoints to add and list tasks.",
        "deadline_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
    }
    cid = client.post("/challenges", headers=recruiter, json=body).json()["id"]
    assert client.post(f"/challenges/{cid}/publish", headers=recruiter).status_code == 200
    client.post(f"/challenges/{cid}/invitations", headers=recruiter, json={"developer_ids": [dev_id]})
    invite = client.get("/assessments", headers=dev).json()["invitations"][0]
    assert client.post(f"/assessments/invitations/{invite['id']}/decline", headers=dev).status_code == 200

    replies = _notes(client, recruiter, "challenge_response")
    assert len(replies) == 1 and "declined" in replies[0]["message"] and replies[0]["related_id"] == cid
