"""Replaceable face-analysis protocol. Implementations live in sibling modules."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import Protocol, runtime_checkable

import numpy as np


class FaceStatus(StrEnum):
    FACE_FOUND = "FACE_FOUND"
    NO_FACE = "NO_FACE"
    MULTIPLE_FACES = "MULTIPLE_FACES"
    UNREADABLE = "UNREADABLE"


@dataclass(frozen=True, slots=True)
class FaceAnalysis:
    status: FaceStatus
    face_count: int
    detection_score: float | None
    embedding: np.ndarray | None  # float32, L2-normalised when status is FACE_FOUND


@runtime_checkable
class FaceEngine(Protocol):
    """One pretrained recogniser. Similarity must use the model's own comparison method."""

    name: str

    def analyze(self, image_bytes: bytes) -> FaceAnalysis: ...

    def similarity(self, a: np.ndarray, b: np.ndarray) -> float: ...


@lru_cache
def get_face_engine() -> FaceEngine:
    from app.config import get_settings

    settings = get_settings()
    if settings.face_engine == "opencv_sface":
        from app.faces.opencv_sface import OpenCvSFaceEngine

        return OpenCvSFaceEngine(
            model_dir=settings.face_model_dir,
            detection_min_score=settings.face_detection_min_score,
            min_size_px=settings.face_min_size_px,
        )
    raise ValueError(f"Unsupported FACE_ENGINE={settings.face_engine!r}")
