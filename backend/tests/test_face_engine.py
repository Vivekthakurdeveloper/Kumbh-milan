"""Real-model face engine tests. Skipped when models or fixtures are missing."""

from pathlib import Path

import cv2
import numpy as np
import pytest

from app.faces.engine import FaceStatus
from app.faces.opencv_sface import OpenCvSFaceEngine

BACKEND = Path(__file__).resolve().parents[1]
MODEL_DIR = BACKEND / "models" / "faces"
FIXTURES = BACKEND / "tests" / "fixtures" / "faces"
REQUIRED = ("person_a_1.jpg", "person_a_2.jpg", "person_b_1.jpg", "no_face.jpg")
MODELS = ("face_detection_yunet_2023mar.onnx", "face_recognition_sface_2021dec.onnx")


def _ready() -> bool:
    return all((MODEL_DIR / name).is_file() for name in MODELS) and all(
        (FIXTURES / name).is_file() for name in REQUIRED
    )


pytestmark = pytest.mark.skipif(not _ready(), reason="Run: python -m app.cli fetch-face-models && fetch-face-fixtures")


@pytest.fixture(scope="module")
def engine() -> OpenCvSFaceEngine:
    return OpenCvSFaceEngine(model_dir=str(MODEL_DIR), detection_min_score=0.9, min_size_px=40)


def _bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_valid_face_detection_and_embedding(engine):
    result = engine.analyze(_bytes("person_a_1.jpg"))
    assert result.status is FaceStatus.FACE_FOUND
    assert result.face_count == 1
    assert result.detection_score is not None and result.detection_score >= 0.9
    assert result.embedding is not None and result.embedding.shape == (128,)
    assert result.embedding.dtype == np.float32


def test_photograph_without_a_face(engine):
    result = engine.analyze(_bytes("no_face.jpg"))
    assert result.status is FaceStatus.NO_FACE
    assert result.face_count == 0
    assert result.embedding is None


def test_multiple_faces_in_one_photograph(engine):
    img_a = cv2.imdecode(np.frombuffer(_bytes("person_a_1.jpg"), np.uint8), cv2.IMREAD_COLOR)
    img_b = cv2.imdecode(np.frombuffer(_bytes("person_b_1.jpg"), np.uint8), cv2.IMREAD_COLOR)
    height = min(img_a.shape[0], img_b.shape[0])
    img_a = cv2.resize(img_a, (int(img_a.shape[1] * height / img_a.shape[0]), height))
    img_b = cv2.resize(img_b, (int(img_b.shape[1] * height / img_b.shape[0]), height))
    ok, encoded = cv2.imencode(".jpg", np.hstack([img_a, img_b]))
    assert ok
    result = engine.analyze(encoded.tobytes())
    assert result.status is FaceStatus.MULTIPLE_FACES
    assert result.face_count >= 2
    assert result.embedding is None


def test_same_person_similarity_above_threshold(engine):
    a1 = engine.analyze(_bytes("person_a_1.jpg"))
    a2 = engine.analyze(_bytes("person_a_2.jpg"))
    score = engine.similarity(a1.embedding, a2.embedding)
    assert score >= 0.363
    assert score <= 1.0


def test_different_person_similarity_below_threshold(engine):
    a1 = engine.analyze(_bytes("person_a_1.jpg"))
    b1 = engine.analyze(_bytes("person_b_1.jpg"))
    score = engine.similarity(a1.embedding, b1.embedding)
    assert score < 0.363


def test_unreadable_bytes(engine):
    result = engine.analyze(b"not-an-image")
    assert result.status is FaceStatus.UNREADABLE
