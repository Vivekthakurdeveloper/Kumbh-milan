from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.common.errors import ServiceUnavailable
from app.config import get_settings


@lru_cache
def _fernet() -> Fernet:
    key = get_settings().contact_encryption_key
    if not key:
        raise ServiceUnavailable(
            "Contact encryption is not configured", code="contact_encryption_unconfigured"
        )
    return Fernet(key.encode())


def encrypt_text(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_text(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ServiceUnavailable(
            "Stored contact details could not be decrypted", code="contact_decryption_failed"
        ) from exc
