"""OpenCV YuNet detector + SFace recogniser (ONNX, CPU). Apache-2.0 / MIT via opencv_zoo."""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

from app.faces.engine import FaceAnalysis, FaceStatus

logger = logging.getLogger("kumbh_setu.faces")

MODEL_NAME = "opencv_sface_2021dec"
YUNET_FILENAME = "face_detection_yunet_2023mar.onnx"
SFACE_FILENAME = "face_recognition_sface_2021dec.onnx"
MAX_SIDE_PX = 1280


class OpenCvSFaceEngine:
    """Lazy-loaded YuNet + SFace. Exactly one usable face is required for an embedding."""

    name = MODEL_NAME

    def __init__(self, *, model_dir: str, detection_min_score: float, min_size_px: int) -> None:
        self._model_dir = Path(model_dir)
        self._detection_min_score = detection_min_score
        self._min_size_px = min_size_px
        self._detector: cv2.FaceDetectorYN | None = None
        self._recognizer: cv2.FaceRecognizerSF | None = None

    def _load(self) -> tuple[cv2.FaceDetectorYN, cv2.FaceRecognizerSF]:
        if self._detector is not None and self._recognizer is not None:
            return self._detector, self._recognizer
        yunet = self._model_dir / YUNET_FILENAME
        sface = self._model_dir / SFACE_FILENAME
        if not yunet.is_file() or not sface.is_file():
            raise FileNotFoundError(
                f"Face models missing under {self._model_dir}. "
                "Run: python -m app.cli fetch-face-models"
            )
        # Input size is overwritten per image via setInputSize.
        self._detector = cv2.FaceDetectorYN.create(
            str(yunet), "", (320, 320), self._detection_min_score, 0.3, 5000
        )
        self._recognizer = cv2.FaceRecognizerSF.create(str(sface), "")
        return self._detector, self._recognizer

    def analyze(self, image_bytes: bytes) -> FaceAnalysis:
        image = _decode(image_bytes)
        if image is None:
            return FaceAnalysis(FaceStatus.UNREADABLE, 0, None, None)
        image = _downscale(image, MAX_SIDE_PX)
        try:
            detector, recognizer = self._load()
        except FileNotFoundError:
            logger.exception("Face models unavailable")
            return FaceAnalysis(FaceStatus.UNREADABLE, 0, None, None)

        height, width = image.shape[:2]
        detector.setInputSize((width, height))
        _, faces = detector.detect(image)
        usable = _usable_faces(faces, self._detection_min_score, self._min_size_px)
        if not usable:
            return FaceAnalysis(FaceStatus.NO_FACE, 0, None, None)
        if len(usable) > 1:
            return FaceAnalysis(FaceStatus.MULTIPLE_FACES, len(usable), usable[0][1], None)

        face_row, score = usable[0]
        aligned = recognizer.alignCrop(image, face_row)
        feature = recognizer.feature(aligned)
        embedding = np.asarray(feature, dtype=np.float32).reshape(-1)
        return FaceAnalysis(FaceStatus.FACE_FOUND, 1, float(score), embedding)

    def similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        _, recognizer = self._load()
        left = np.asarray(a, dtype=np.float32).reshape(1, -1)
        right = np.asarray(b, dtype=np.float32).reshape(1, -1)
        return float(recognizer.match(left, right, cv2.FaceRecognizerSF_FR_COSINE))


def _decode(image_bytes: bytes) -> np.ndarray | None:
    if not image_bytes:
        return None
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return image


def _downscale(image: np.ndarray, max_side: int) -> np.ndarray:
    height, width = image.shape[:2]
    longest = max(height, width)
    if longest <= max_side:
        return image
    scale = max_side / longest
    return cv2.resize(image, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)


def _usable_faces(
    faces: np.ndarray | None, min_score: float, min_size_px: int
) -> list[tuple[np.ndarray, float]]:
    """YuNet rows: x, y, w, h, landmarks..., score. Keep faces that pass score and size filters."""
    if faces is None:
        return []
    usable: list[tuple[np.ndarray, float]] = []
    for row in faces:
        score = float(row[-1])
        width, height = float(row[2]), float(row[3])
        if score < min_score:
            continue
        if min(width, height) < min_size_px:
            continue
        usable.append((row, score))
    usable.sort(key=lambda item: item[1], reverse=True)
    return usable
