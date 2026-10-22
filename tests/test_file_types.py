"""What a file is, from its bytes, and which types may be shown inline."""

from app.services.file_types import blocked_edit_reason, classify, delivery


def test_signature_wins_and_text_is_not_trusted_from_the_extension():
    html = b"<html><script>alert(1)</script></html>"
    assert classify("notes.png", html).kind == "text"
    assert classify("readme", b"hello\n").kind == "text"
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    assert classify("notes.txt", png).kind == "image"
    assert classify("logo.PNG", b"").kind == "image"


def test_active_content_is_plain_text_unless_downloaded():
    info = classify("page.html", b"<html></html>")
    assert delivery(info, "page.html", False) == ("text/plain", True)
    assert delivery(info, "page.html", True) == ("application/octet-stream", False)
    svg = classify("icon.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>")
    assert delivery(svg, "icon.svg", False) == ("text/plain", True)


def test_workflows_and_secrets_cannot_be_edited():
    assert "Workflow" in (blocked_edit_reason(".github/workflows/ci.yml") or "")
    assert "Secret" in (blocked_edit_reason(".env") or "")
    assert "Secret" in (blocked_edit_reason("keys/app.pem") or "")
    assert blocked_edit_reason("src/app.py") is None
    assert blocked_edit_reason(".env.example") is None
