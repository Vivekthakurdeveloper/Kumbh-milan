"""Encrypt and decrypt facial embedding vectors. Never return plaintext through APIs."""

from __future__ import annotations

from functools import lru_cache

import numpy as np
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.common.crypto import build_keyring
from app.common.errors import ServiceUnavailable
from app.config import get_settings


def _face_keys() -> tuple[str, list[str]]:
    settings = get_settings()
    if settings.face_embedding_encryption_key:
        return settings.face_embedding_encryption_key, settings.face_embedding_encryption_previous_keys
    return settings.contact_encryption_key, settings.contact_encryption_previous_keys


@lru_cache
def face_keyring() -> MultiFernet:
    primary, previous = _face_keys()
    if not primary:
        raise ServiceUnavailable(
            "Face embedding encryption is not configured", code="face_encryption_unconfigured"
        )
    return build_keyring(primary, previous)


@lru_cache
def face_primary() -> Fernet:
    face_keyring()
    return Fernet(_face_keys()[0].encode())


def encrypt_embedding(embedding: np.ndarray) -> str:
    payload = np.asarray(embedding, dtype=np.float32).tobytes()
    return face_keyring().encrypt(payload).decode("ascii")


def decrypt_embedding(ciphertext: str) -> np.ndarray:
    try:
        raw = face_keyring().decrypt(ciphertext.encode("ascii"))
    except InvalidToken as exc:
        raise ServiceUnavailable(
            "Stored face embedding could not be decrypted", code="face_decryption_failed"
        ) from exc
    return np.frombuffer(raw, dtype=np.float32).copy()
