from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.common.errors import ServiceUnavailable
from app.config import get_settings


def build_keyring(primary: str, previous: list[str]) -> MultiFernet:
    """Encrypts with the primary key; decrypts with the primary or any previous key during a rotation."""
    return MultiFernet([Fernet(primary.encode()), *(Fernet(k.encode()) for k in previous if k)])


@lru_cache
def contact_keyring() -> MultiFernet:
    settings = get_settings()
    if not settings.contact_encryption_key:
        raise ServiceUnavailable(
            "Contact encryption is not configured", code="contact_encryption_unconfigured"
        )
    return build_keyring(settings.contact_encryption_key, settings.contact_encryption_previous_keys)


@lru_cache
def contact_primary() -> Fernet:
    contact_keyring()
    return Fernet(get_settings().contact_encryption_key.encode())


def encrypt_text(plaintext: str) -> str:
    return contact_keyring().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_text(ciphertext: str) -> str:
    try:
        return contact_keyring().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ServiceUnavailable(
            "Stored contact details could not be decrypted", code="contact_decryption_failed"
        ) from exc


def reset_keyrings() -> None:
    from app.faces import crypto as face_crypto

    contact_keyring.cache_clear()
    contact_primary.cache_clear()
    face_crypto.face_keyring.cache_clear()
    face_crypto.face_primary.cache_clear()
