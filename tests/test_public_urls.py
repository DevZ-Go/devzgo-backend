"""Public URL helpers never keep localhost callbacks when running on Render."""

import app.core.public_urls as urls


def test_backend_prefers_explicit_public_url(monkeypatch):
    monkeypatch.setenv("BACKEND_URL", "https://devzgo-backend.onrender.com")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://other.onrender.com")
    monkeypatch.delenv("RENDER", raising=False)
    assert urls.backend_base() == "https://devzgo-backend.onrender.com"


def test_backend_ignores_localhost_on_render(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("BACKEND_URL", "http://localhost:8000")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://devzgo-backend.onrender.com")
    assert urls.backend_base() == "https://devzgo-backend.onrender.com"


def test_frontend_defaults_to_www_on_render_when_unset(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.delenv("FRONTEND_URL", raising=False)
    assert urls.frontend_base() == "https://www.devzgo.com"
