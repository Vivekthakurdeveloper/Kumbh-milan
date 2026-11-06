"""Download public-domain face photographs for integration tests."""

from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path

# Public-domain / US-government work portraits on Wikimedia Commons. SHA-256 pins prevent silent swaps.
FIXTURES: tuple[tuple[str, str, str], ...] = (
    (
        "person_a_1.jpg",
        "https://upload.wikimedia.org/wikipedia/commons/8/8d/President_Barack_Obama.jpg",
        "744dd848fbb0584229169e01c4944664957c62495fb9e8af514a088ebca43e19",
    ),
    (
        "person_a_2.jpg",
        "https://upload.wikimedia.org/wikipedia/commons/e/e9/Official_portrait_of_Barack_Obama.jpg",
        "19ec613e6831dd0c285b907f4cf1be13a654f8ac237435146320ec3dcc42cd45",
    ),
    (
        "person_b_1.jpg",
        "https://upload.wikimedia.org/wikipedia/commons/d/d4/George-W-Bush.jpeg",
        "59207a3f480eb56547b4e38c6e82ecb6871a3a37c5dcce6c682207fa8c8a75b4",
    ),
    (
        "no_face.jpg",
        "https://upload.wikimedia.org/wikipedia/commons/0/0a/The_Great_Wave_off_Kanagawa.jpg",
        "44a8486abdf0330a3fe6b586e407506e738fe075fe0b5dfc43961e358fda7206",
    ),
)


def fetch_fixtures(dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename, url, expected_sha in FIXTURES:
        path = dest / filename
        if path.is_file():
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


def fetch_fixtures_cli(dest: Path, *, force: bool = False) -> list[Path]:
    if force:
        for filename, _, _ in FIXTURES:
            path = dest / filename
            if path.exists():
                path.unlink()
    return fetch_fixtures(dest)


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "kumbh-setu-face-fixtures/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310 - pinned HTTPS URLs
        return response.read()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
