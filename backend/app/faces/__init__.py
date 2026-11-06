"""Facial analysis: detect a usable face, embed it, and compare embeddings.

The case-management APIs never depend on a specific model. Callers use
`get_face_engine()` and the `FaceEngine` protocol; swap models via FACE_ENGINE.
"""

from app.faces.engine import FaceAnalysis, FaceEngine, FaceStatus, get_face_engine

__all__ = [
    "FaceAnalysis",
    "FaceEngine",
    "FaceStatus",
    "get_face_engine",
]
