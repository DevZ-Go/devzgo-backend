"""Same password, different hash. bcrypt keeps the salt inside the hash."""

from app.core.security import hash_password, verify_password


def test_equal_passwords_do_not_share_a_hash():
    first = hash_password("Password1!")
    second = hash_password("Password1!")
    assert first != second
    assert first.startswith("$2")
    assert verify_password("Password1!", first)
    assert verify_password("Password1!", second)
    assert not verify_password("Password2!", first)
