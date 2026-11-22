"""Club announcements, polls, presidency transfer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _create_club_with_member(client, make_user):
    president, _ = make_user()
    member, member_id = make_user()
    res = client.post(
        "/groups",
        headers=president,
        json={
            "name": "Feature Test Club",
            "tagline": "Testing",
            "about": "About",
            "category": "general",
            "joinPolicy": "open",
        },
    )
    assert res.status_code == 200, res.text
    club = res.json()
    club_id = club["id"]
    join = client.post(f"/groups/{club_id}/join", headers=member)
    assert join.status_code == 200, join.text
    return club_id, president, member, member_id


def test_announcement_requires_exec(client, make_user):
    club_id, president, member, _ = _create_club_with_member(client, make_user)
    denied = client.post(
        f"/groups/{club_id}/announcements",
        headers=member,
        json={"title": "Nope", "content": "Members cannot post"},
    )
    assert denied.status_code == 403

    ok = client.post(
        f"/groups/{club_id}/announcements",
        headers=president,
        json={"title": "Hello", "content": "Welcome", "badge": "General Update"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["title"] == "Hello"

    listed = client.get(f"/groups/{club_id}/announcements", headers=member)
    assert listed.status_code == 200
    assert any(a["title"] == "Hello" for a in listed.json())


def test_poll_vote_unique(client, make_user):
    club_id, president, member, _ = _create_club_with_member(client, make_user)
    poll = client.post(
        f"/groups/{club_id}/polls",
        headers=president,
        json={"question": "Pizza?", "options": ["Yes", "No"]},
    )
    assert poll.status_code == 200, poll.text
    body = poll.json()
    poll_id = body["id"]
    option_a = body["options"][0]["id"]
    option_b = body["options"][1]["id"]

    first = client.post(
        f"/groups/{club_id}/polls/{poll_id}/vote",
        headers=member,
        json={"optionId": option_a},
    )
    assert first.status_code == 200, first.text
    assert first.json()["myVoteOptionId"] == option_a

    second = client.post(
        f"/groups/{club_id}/polls/{poll_id}/vote",
        headers=member,
        json={"optionId": option_b},
    )
    assert second.status_code == 200, second.text
    assert second.json()["myVoteOptionId"] == option_b


def test_presidency_transfer_leaves_one_president(client, make_user):
    club_id, president, member, member_id = _create_club_with_member(client, make_user)
    transferred = client.post(
        f"/groups/{club_id}/transfer-presidency",
        headers=president,
        json={"targetUserId": member_id},
    )
    assert transferred.status_code == 200, transferred.text
    members = transferred.json()["members"]
    presidents = [m for m in members if m["role"] in ("host", "president")]
    assert len(presidents) == 1
    assert presidents[0]["userId"] == member_id


def test_event_create_and_list(client, make_user):
    club_id, president, _, _ = _create_club_with_member(client, make_user)
    starts = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    created = client.post(
        f"/groups/{club_id}/events",
        headers=president,
        json={
            "title": "Hack night",
            "description": "Build together",
            "startsAt": starts,
            "status": "published",
        },
    )
    assert created.status_code == 200, created.text
    listed = client.get(f"/groups/{club_id}/events?when=upcoming", headers=president)
    assert listed.status_code == 200
    assert any(e["title"] == "Hack night" for e in listed.json())


def test_groups_list_paginated(client, make_user):
    headers, _ = make_user()
    res = client.get("/groups", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert "items" in body
    assert "total" in body
