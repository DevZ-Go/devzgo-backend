from app.services.password_reset_email import password_reset_html


def test_reset_email_is_branded_and_escapes_username(monkeypatch):
    monkeypatch.setenv("BACKEND_URL", "https://devzgo-backend.onrender.com")
    html = password_reset_html('ada<img src=x>', "https://www.devzgo.com/reset-password?token=abc")
    assert "https://devzgo-backend.onrender.com/static/brand/email-logo.png" in html
    assert "Reset password" in html
    assert "ada&lt;img src=x&gt;" in html
    assert "<img src=x>" not in html
    assert "token=abc" in html
