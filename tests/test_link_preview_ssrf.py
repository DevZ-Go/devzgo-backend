"""SSRF guards for link preview helper."""

import pytest

from app.services.link_preview import _assert_public_host, _normalize


def test_normalize_rejects_non_http():
    with pytest.raises(ValueError):
        _normalize("file:///etc/passwd")
    with pytest.raises(ValueError):
        _normalize("ftp://example.com/a")


def test_normalize_strips_fragment():
    assert _normalize("https://example.com/path#frag") == "https://example.com/path"


def test_assert_public_host_blocks_localhost():
    with pytest.raises(ValueError):
        _assert_public_host("localhost")


def test_assert_public_host_blocks_loopback_ip():
    with pytest.raises(ValueError):
        _assert_public_host("127.0.0.1")
