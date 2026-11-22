"""CORS allow-list includes production frontends and reflects Authorization on preflight."""

from app.main import _cors_origins, app
from fastapi.testclient import TestClient


def test_production_origins_are_allowlisted():
    origins = _cors_origins()
    assert "https://www.devzgo.com" in origins
    assert "https://devzgo.com" in origins
    assert "https://devzgo-frontend.pages.dev" in origins


def test_preflight_profiles_me_from_www():
    client = TestClient(app)
    res = client.options(
        "/profiles/me",
        headers={
            "Origin": "https://www.devzgo.com",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == "https://www.devzgo.com"
    assert res.headers.get("access-control-allow-credentials") == "true"
    allowed = (res.headers.get("access-control-allow-headers") or "").lower()
    assert "authorization" in allowed
    assert "content-type" in allowed


def test_unauthorized_get_still_has_cors_headers():
    """401 responses must still carry ACAO so the browser can read the error body."""
    client = TestClient(app)
    res = client.get("/profiles/me", headers={"Origin": "https://www.devzgo.com"})
    assert res.status_code == 401
    assert res.headers.get("access-control-allow-origin") == "https://www.devzgo.com"
    assert res.headers.get("access-control-allow-credentials") == "true"


def test_apex_and_pages_preview_allowed():
    client = TestClient(app)
    for origin in ("https://devzgo.com", "https://devzgo-frontend.pages.dev"):
        res = client.options(
            "/auth/login",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert res.status_code == 200, origin
        assert res.headers.get("access-control-allow-origin") == origin


def test_unhandled_500_still_has_cors_headers(monkeypatch):
    """Bare 500s used to omit ACAO and browsers reported a fake CORS error."""
    from fastapi import APIRouter

    boom = APIRouter()

    @boom.get("/__cors_boom_test")
    def _boom():
        raise RuntimeError("forced failure for cors test")

    app.include_router(boom)
    client = TestClient(app, raise_server_exceptions=False)
    res = client.get("/__cors_boom_test", headers={"Origin": "https://www.devzgo.com"})
    assert res.status_code == 500
    assert res.headers.get("access-control-allow-origin") == "https://www.devzgo.com"
    assert res.headers.get("access-control-allow-credentials") == "true"

