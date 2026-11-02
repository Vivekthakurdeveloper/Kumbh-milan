from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.common.errors import ValidationFailed
from app.config import get_settings

_hasher = PasswordHasher()  # Argon2id with library-recommended parameters

MAX_PASSWORD_LENGTH = 128


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


@lru_cache
def dummy_hash() -> str:
    """Verified against for unknown usernames so response timing does not reveal which accounts exist."""
    return _hasher.hash("kumbh-setu-timing-equaliser")


def validate_new_password(password: str, *, username: str) -> None:
    min_length = get_settings().password_min_length
    if len(password) < min_length:
        raise ValidationFailed(f"Password must be at least {min_length} characters", code="weak_password")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValidationFailed("Password is too long", code="weak_password")
    if username.lower() in password.lower():
        raise ValidationFailed("Password must not contain the username", code="weak_password")
    classes = sum(
        [
            any(c.islower() for c in password),
            any(c.isupper() for c in password),
            any(c.isdigit() for c in password),
            any(not c.isalnum() for c in password),
        ]
    )
    if classes < 3:
        raise ValidationFailed(
            "Password must mix at least three of: lowercase, uppercase, digits, symbols",
            code="weak_password",
        )
