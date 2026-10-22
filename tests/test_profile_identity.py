"""A person can set a role and upload a profile photo, and cannot find themselves in search."""
import io


def test_role_and_avatar(client, make_user):
    headers, user_id = make_user()
    saved = client.put("/profiles/me", headers=headers, json={"role": "Designer", "full_name": "Ada"}).json()
    assert saved["role"] == "Designer" and saved["full_name"] == "Ada"
    bad = client.put("/profiles/me", headers=headers, json={"role": "Wizard"})
    assert bad.status_code == 400

    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x00\x03\x00\x01"
        b"\x00\x05\xfe\x02\xfe\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    uploaded = client.post(
        "/profiles/me/avatar",
        headers=headers,
        files={"file": ("me.png", io.BytesIO(png), "image/png")},
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["avatar_url"].startswith("/storage/avatars/")
    assert uploaded.json()["role"] == "Designer"

    me = client.get("/profiles/me", headers=headers).json()
    found = client.get("/profiles/search", headers=headers, params={"query": me["username"]}).json()
    assert user_id not in [row["id"] for row in found]
    assert client.post("/connections/request", headers=headers, json={"target_user_id": user_id}).status_code == 400
