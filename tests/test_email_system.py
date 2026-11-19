"""Unit tests for OTP hashing, templates, and unsubscribe tokens (no DB)."""

from app.services.email_templates import render_template
from app.services.otp import generate_otp, hash_otp, mask_email, otp_matches
from app.services.unsubscribe import CATEGORIES, make_token, parse_token


def test_otp_round_trip(monkeypatch):
    monkeypatch.setenv("EMAIL_OTP_PEPPER", "test-pepper")
    code = generate_otp()
    assert len(code) == 6 and code.isdigit()
    digest = hash_otp(code, subject="Ada@Example.com")
    assert otp_matches(code, subject="ada@example.com", expected_hash=digest)
    assert not otp_matches("000000", subject="ada@example.com", expected_hash=digest)


def test_mask_email():
    assert mask_email("chan@devzgo.com").startswith("c***@")
    assert "@devzgo.com" in mask_email("chan@devzgo.com")


def test_unsubscribe_token_round_trip(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-for-unsub")
    from uuid import uuid4

    user_id = uuid4()
    category = "connection_requests"
    assert category in CATEGORIES
    token = make_token(user_id, category)
    parsed = parse_token(token)
    assert parsed == (user_id, category)
    assert parse_token("not-a-token") is None


def test_verification_template_escapes_and_shows_code(monkeypatch):
    monkeypatch.setenv("FRONTEND_URL", "https://www.devzgo.com")
    subject, html, text = render_template(
        "verification_otp",
        {"username": 'ada<img src=x>', "code": "123456"},
    )
    assert "123456" in html and "123456" in text
    assert "ada&lt;img src=x&gt;" in html
    assert "<img src=x>" not in html
    assert "Verify" in subject or "verification" in subject.lower()


def test_welcome_and_password_changed_templates(monkeypatch):
    monkeypatch.setenv("FRONTEND_URL", "https://www.devzgo.com")
    _, welcome_html, _ = render_template("welcome", {"username": "janasi"})
    assert "Welcome" in welcome_html
    _, changed_html, _ = render_template("password_changed", {"username": "janasi"})
    assert "password" in changed_html.lower()
