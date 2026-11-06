"""Download and verify pinned OpenCV Zoo face models."""

from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path

# opencv_zoo (Apache-2.0). SHA-256 pins prevent silent model swaps.
MODELS: tuple[tuple[str, str, str], ...] = (
    (
        "face_detection_yunet_2023mar.onnx",
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    ),
    (
        "face_recognition_sface_2021dec.onnx",
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    ),
)


def fetch_models(dest: Path, *, force: bool = False) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename, url, expected_sha in MODELS:
        path = dest / filename
        if path.is_file() and not force:
            actual = _sha256(path)
            if actual != expected_sha:
                raise RuntimeError(f"{path} has SHA-256 {actual}, expected {expected_sha}")
            written.append(path)
            continue
        data = _download(url)
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected_sha:
            raise RuntimeError(f"Downloaded {filename} has SHA-256 {actual}, expected {expected_sha}")
        path.write_bytes(data)
        written.append(path)
    return written


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "kumbh-setu-face-models/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310 - pinned HTTPS URLs
        return response.read()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
