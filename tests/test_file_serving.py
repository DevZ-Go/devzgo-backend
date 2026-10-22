"""Serving stored project files: who may read them, safe headers, byte ranges and preview limits."""

import io
import stat
import zipfile
from urllib.parse import parse_qs, urlparse

import pytest

from app.services.file_responses import RangeNotSatisfiable, content_type_for, parse_range

PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4
BIG = 2 * 1024 * 1024
FILES = {
    "src/app.py": b"print('hi')\n",
    "logo.png": PNG,
    "page.html": b"<script>alert(1)</script>",
    "icon.svg": b"<svg xmlns='http://www.w3.org/2000/svg' onload='alert(1)'/>",
    "empty.txt": b"",
    ".env": b"API_KEY=do-not-show\n",
    "big.txt": b"a" * (BIG - 1) + "é".encode() * 10,
}


def _zip(entries: dict[str, bytes], symlink: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, b"src/app.py")
    return buf.getvalue()


def _upload(client, headers, pid, data):
    return client.post(f"/projects/{pid}/workspace/upload", headers=headers,
                       files={"file": ("source.zip", data, "application/zip")})


def _project(client, headers, visibility="Private"):
    pid = client.post("/projects", headers=headers,
                      json={"title": "Files", "category": "Gaming", "visibility": visibility}).json()["id"]
    assert _upload(client, headers, pid, _zip(FILES)).status_code == 200
    return pid


def _invite(client, owner, pid, member, access):
    name = client.get("/auth/me", headers=member).json()["username"]
    inv = client.post(f"/collaborations/projects/{pid}/invitations", headers=owner,
                      json={"username": name, "access": access}).json()
    assert client.put(f"/collaborations/invitations/{inv['id']}/accept", headers=member).status_code == 200


def _raw(client, pid, path, headers=None, **extra):
    return client.get(f"/projects/{pid}/file/raw", params={"path": path}, headers={**(headers or {}), **extra})


def test_parse_range():
    assert parse_range(None, 100) is None
    assert parse_range("bytes=0-9", 100) == (0, 9)
    assert parse_range("bytes=90-", 100) == (90, 99)
    assert parse_range("bytes=95-500", 100) == (95, 99)
    assert parse_range("bytes=-10", 100) == (90, 99)
    assert parse_range("bytes=-500", 100) == (0, 99)
    assert parse_range("bytes=0-1,5-6", 100) is None
    assert parse_range("bytes=9-2", 100) is None
    assert parse_range("items=0-9", 100) is None
    assert parse_range("bytes=0-9", 0) is None
    for header in ("bytes=100-", "bytes=-0"):
        with pytest.raises(RangeNotSatisfiable):
            parse_range(header, 100)


def test_only_passive_types_are_inline():
    assert content_type_for("a/logo.PNG") == ("image/png", True)
    assert content_type_for("doc.pdf") == ("application/pdf", True)
    assert content_type_for("clip.webm") == ("video/webm", True)
    assert content_type_for("icon.svg") == ("text/plain", True)
    for name in ("page.html", "page.htm", "feed.xml", "app.js", "README"):
        assert content_type_for(name) == ("text/plain", True)


def test_raw_headers_and_ranges(client, make_user):
    owner, _ = make_user()
    pid = _project(client, owner)

    full = _raw(client, pid, "logo.png", owner)
    assert full.status_code == 200 and full.content == PNG
    assert full.headers["content-type"] == "image/png"
    assert full.headers["content-disposition"].startswith("inline;")
    assert full.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in full.headers["content-security-policy"]
    assert full.headers["accept-ranges"] == "bytes"
    assert full.headers["content-length"] == str(len(PNG))

    part = _raw(client, pid, "logo.png", owner, Range="bytes=0-7")
    assert part.status_code == 206 and part.content == PNG[:8]
    assert part.headers["content-range"] == f"bytes 0-7/{len(PNG)}"
    tail = _raw(client, pid, "logo.png", owner, Range="bytes=-4")
    assert tail.status_code == 206 and tail.content == PNG[-4:]
    assert _raw(client, pid, "logo.png", owner, Range="bytes=0-1,4-5").status_code == 200
    beyond = _raw(client, pid, "logo.png", owner, Range=f"bytes={len(PNG)}-")
    assert beyond.status_code == 416 and beyond.headers["content-range"] == f"bytes */{len(PNG)}"

    html = _raw(client, pid, "page.html", owner)
    assert html.headers["content-type"].startswith("text/plain")
    assert html.headers["content-disposition"].startswith("inline;")
    svg = _raw(client, pid, "icon.svg", owner)
    assert svg.headers["content-type"].startswith("text/plain")
    assert svg.headers["content-disposition"].startswith("inline;")
    downloaded = client.get(f"/projects/{pid}/file/raw", params={"path": "page.html", "download": "true"}, headers=owner)
    assert downloaded.headers["content-type"] == "application/octet-stream"
    assert downloaded.headers["content-disposition"].startswith("attachment;")

    empty = _raw(client, pid, "empty.txt", owner, Range="bytes=0-5")
    assert empty.status_code == 200 and empty.content == b""

    assert _raw(client, pid, "../etc/passwd", owner).status_code == 400
    assert _raw(client, pid, "missing.txt", owner).status_code == 404


def test_preview_reads_only_the_head(client, make_user):
    owner, _ = make_user()
    pid = _project(client, owner)

    big = client.get(f"/projects/{pid}/file", params={"path": "big.txt"}, headers=owner).json()
    assert big["size"] == len(FILES["big.txt"])
    assert not big["is_binary"] and "truncated" in big["message"]
    # The cut falls inside a two-byte character, which is dropped rather than failing the preview.
    assert big["content"] == "a" * (BIG - 1)

    small = client.get(f"/projects/{pid}/file", params={"path": "src/app.py"}, headers=owner).json()
    assert small["content"] == "print('hi')\n" and small["message"] is None
    image = client.get(f"/projects/{pid}/file", params={"path": "logo.png"}, headers=owner).json()
    assert image["is_binary"] and image["content"] is None


def test_private_project_files_need_membership(client, make_user, make_recruiter):
    owner, _ = make_user()
    stranger, _ = make_user()
    recruiter, _ = make_recruiter()
    viewer, _ = make_user()
    editor, _ = make_user()
    pid = _project(client, owner)
    _invite(client, owner, pid, viewer, "viewer")
    _invite(client, owner, pid, editor, "editor")

    for headers in (None, stranger, recruiter):
        assert _raw(client, pid, "src/app.py", headers).status_code in (401, 403)
        assert client.get(f"/projects/{pid}/file", params={"path": "src/app.py"}, headers=headers).status_code in (401, 403)
        assert client.get(f"/projects/{pid}/files", headers=headers).status_code in (401, 403)

    assert _raw(client, pid, "src/app.py", viewer).status_code == 200
    assert _raw(client, pid, ".env", viewer).status_code == 403
    hidden = client.get(f"/projects/{pid}/file", params={"path": ".env"}, headers=viewer).json()
    assert hidden["is_secret"] and hidden["content"] is None
    assert _raw(client, pid, ".env", owner).content == FILES[".env"]

    assert _upload(client, viewer, pid, _zip(FILES)).status_code == 403
    assert _upload(client, recruiter, pid, _zip(FILES)).status_code in (403, 404)
    assert _upload(client, editor, pid, _zip(FILES)).status_code == 200


def test_public_project_hides_secrets_from_visitors(client, make_user, make_recruiter):
    owner, _ = make_user()
    recruiter, _ = make_recruiter()
    pid = _project(client, owner, visibility="Public")

    for headers in (None, recruiter):
        assert _raw(client, pid, "src/app.py", headers).status_code == 200
        assert _raw(client, pid, ".env", headers).status_code == 403
    assert _upload(client, recruiter, pid, _zip(FILES)).status_code in (403, 404)


def test_zip_upload_records_source_and_failed_upload_keeps_files(client, make_user):
    owner, _ = make_user()
    visitor, _ = make_user()
    pid = _project(client, owner, visibility="Public")

    source = client.get(f"/projects/{pid}", headers=owner).json()["source"]
    assert source["kind"] == "zip" and source["sync_state"] == "ok"
    assert source["snapshot"]["origin"] == "zip" and source["snapshot"]["commit_sha"] is None

    bad = _upload(client, owner, pid, _zip({"src/app.py": b"print('new')\n"}, symlink="link.py"))
    assert bad.status_code == 400
    assert _raw(client, pid, "src/app.py", owner).content == FILES["src/app.py"]

    failed = client.get(f"/projects/{pid}", headers=owner).json()["source"]
    assert failed["sync_state"] == "failed" and "symlink" in failed["sync_error"].lower()
    seen = client.get(f"/projects/{pid}", headers=visitor).json()["source"]
    assert seen["sync_state"] == "failed" and seen["sync_error"] is None

    assert _upload(client, owner, pid, _zip(FILES)).status_code == 200
    assert client.get(f"/projects/{pid}", headers=owner).json()["source"]["sync_error"] is None


def test_meta_stream_token_and_archive_listing(client, make_user):
    owner, _ = make_user()
    pid = client.post("/projects", headers=owner, json={"title": "Kinds", "category": "Gaming", "visibility": "Private"}).json()["id"]
    payload = _zip({
        "notes.png": b"<html><script>nope</script></html>",
        "page.html": b"<html>ok</html>",
        "bundle.zip": _zip({"a.txt": b"hi"}),
    })
    assert _upload(client, owner, pid, payload).status_code == 200

    meta = client.get(f"/projects/{pid}/files/meta", params={"path": "notes.png"}, headers=owner)
    assert meta.status_code == 200, meta.text
    body = meta.json()
    assert body["kind"] == "text"
    parsed = urlparse(body["stream_url"])
    query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
    streamed = client.get(parsed.path, params=query, headers=owner)
    assert streamed.status_code == 200
    assert streamed.headers["content-type"].startswith("text/plain")
    assert client.get(parsed.path, params={**query, "path": "page.html"}, headers=owner).status_code == 401
    assert client.get(parsed.path, params={**query, "download": "true"}, headers=owner).status_code == 401

    listing = client.get(f"/projects/{pid}/files/archive", params={"path": "bundle.zip"}, headers=owner)
    assert listing.status_code == 200, listing.text
    assert any(item["name"] == "a.txt" for item in listing.json()["entries"])
