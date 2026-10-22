"""Fernet encryption for GitHub tokens and draft text.

The key is GITHUB_TOKEN_ENCRYPTION_KEY, or WORKSPACE_TOKEN_KEY if that is the name already set.
Editing stays unavailable when neither is set, rather than storing tokens in the clear.
"""

from __future__ import annotations

import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken


class EncryptionNotConfigured(RuntimeError):
    """The server has no key for tokens and drafts."""


def _key() -> bytes:
    raw = os.getenv("GITHUB_TOKEN_ENCRYPTION_KEY") or os.getenv("WORKSPACE_TOKEN_KEY") or ""
    if not raw:
        raise EncryptionNotConfigured("Editing isn't configured on this server.")
    return raw.encode()


def configured() -> bool:
    try:
        _key()
    except EncryptionNotConfigured:
        return False
    return True


def encrypt(text: str) -> str:
    return Fernet(_key()).encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return Fernet(_key()).decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise EncryptionNotConfigured("Saved data could not be read. Reconnect GitHub.") from exc


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
