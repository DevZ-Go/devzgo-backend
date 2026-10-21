"""Account rules shared by registration, GitHub onboarding, password reset and password change."""

import re

USERNAME_RE = re.compile(r"^[a-z0-9_](?:[a-z0-9_.-]{1,28}[a-z0-9_])$")
RESERVED_USERNAMES = {
    "admin", "administrator", "api", "auth", "devzgo", "explore", "help", "login", "logout", "me", "messages",
    "network", "new", "notifications", "profile", "projects", "register", "root", "settings", "support", "system", "wrap",
}


def normalize_username(value: str) -> str:
    return (value or "").strip().lower()


def username_problem(value: str) -> str | None:
    """None if the (normalized) username is acceptable, else 'invalid' or 'reserved'."""
    if not USERNAME_RE.match(value):
        return "invalid"
    return "reserved" if value in RESERVED_USERNAMES else None


def password_problem(value: str) -> str | None:
    if not 8 <= len(value) <= 128:
        return "Password must be 8 to 128 characters"
    if not any(c.isupper() for c in value):
        return "Password needs at least one uppercase letter"
    if all(c.isalnum() for c in value):
        return "Password needs at least one special character"
    return None
