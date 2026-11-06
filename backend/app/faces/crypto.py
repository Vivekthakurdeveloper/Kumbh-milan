"""Encrypt and decrypt facial embedding vectors. Never return plaintext through APIs."""

from __future__ import annotations

from functools import lru_cache

import numpy as np
from cryptography.fernet import Fernet, InvalidToken

from app.common.errors import ServiceUnavailable
from app.config import get_settings


@lru_cache
def _fernet() -> Fernet:
    key = get_settings().face_embedding_encryption_key or get_settings().contact_encryption_key
    if not key:
        raise ServiceUnavailable(
            "Face embedding encryption is not configured", code="face_encryption_unconfigured"
        )
    return Fernet(key.encode())


def encrypt_embedding(embedding: np.ndarray) -> str:
    payload = np.asarray(embedding, dtype=np.float32).tobytes()
    return _fernet().encrypt(payload).decode("ascii")


def decrypt_embedding(ciphertext: str) -> np.ndarray:
    try:
        raw = _fernet().decrypt(ciphertext.encode("ascii"))
    except InvalidToken as exc:
        raise ServiceUnavailable(
            "Stored face embedding could not be decrypted", code="face_decryption_failed"
        ) from exc
    return np.frombuffer(raw, dtype=np.float32).copy()
