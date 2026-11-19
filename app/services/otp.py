"""Cryptographic OTP helpers. Never log or return plaintext codes."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone


OTP_TTL = timedelta(minutes=10)
OTP_RESEND = timedelta(seconds=60)
OTP_MAX_ATTEMPTS = 5


def _pepper() -> bytes:
    value = os.getenv("EMAIL_OTP_PEPPER") or os.getenv("SECRET_KEY") or "dev-only-otp-pepper"
    return value.encode("utf-8")


def generate_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp(code: str, *, subject: str) -> str:
    msg = f"{subject.strip().lower()}:{code.strip()}".encode("utf-8")
    return hmac.new(_pepper(), msg, hashlib.sha256).hexdigest()


def otp_matches(code: str, *, subject: str, expected_hash: str) -> bool:
    got = hash_otp(code, subject=subject)
    return hmac.compare_digest(got, expected_hash)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def otp_expiry(from_time: datetime | None = None) -> datetime:
    return (from_time or now_utc()) + OTP_TTL


def resend_at(from_time: datetime | None = None) -> datetime:
    return (from_time or now_utc()) + OTP_RESEND


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    if not domain:
        return "***"
    keep = local[:1] if local else "*"
    return f"{keep}***@{domain}"
