"""
Malware scanning.

Modes:
- clamav: ClamAV clamd over TCP (INSTREAM). Required outside local and test.
- disabled: development only. Files are marked SKIPPED_DEV, which production never treats as approved.
"""

import logging
import socket
import struct
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import Protocol

from app.config import Settings, get_settings

logger = logging.getLogger("kumbh_setu.scanner")
_CHUNK = 64 * 1024


class ScanOutcome(StrEnum):
    CLEAN = "CLEAN"
    INFECTED = "INFECTED"
    ERROR = "ERROR"  # the scanner answered but could not scan this file (e.g. too large); retrying will not help


@dataclass(frozen=True)
class ScanVerdict:
    outcome: ScanOutcome
    signature: str | None = None
    detail: str | None = None


class ScannerUnavailable(Exception):
    """Transient: the scanner could not be reached or timed out. The file stays quarantined for retry."""


class MalwareScanner(Protocol):
    mode: str

    def scan(self, data: bytes) -> ScanVerdict: ...

    def health(self) -> str: ...


class DisabledScanner:
    mode = "disabled"

    def scan(self, data: bytes) -> ScanVerdict:  # pragma: no cover - callers check mode first
        raise ScannerUnavailable("scanning is disabled")

    def health(self) -> str:
        return "disabled_development_mode"


class ClamAVScanner:
    mode = "clamav"

    def __init__(
        self, host: str, port: int, *, connect_timeout: float, scan_timeout: float, max_scan_bytes: int
    ) -> None:
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.scan_timeout = scan_timeout
        self.max_scan_bytes = max_scan_bytes

    def _connect(self) -> socket.socket:
        sock = socket.create_connection((self.host, self.port), timeout=self.connect_timeout)
        sock.settimeout(self.scan_timeout)
        return sock

    @staticmethod
    def _read_reply(sock: socket.socket) -> str:
        reply = b""
        while not reply.endswith(b"\0"):
            part = sock.recv(4096)
            if not part:
                break
            reply += part
        return reply.rstrip(b"\0").decode("utf-8", "replace").strip()

    def ping(self) -> bool:
        try:
            with self._connect() as sock:
                sock.sendall(b"zPING\0")
                return self._read_reply(sock) == "PONG"
        except OSError:
            return False

    def health(self) -> str:
        return "ok" if self.ping() else "unavailable"

    def scan(self, data: bytes) -> ScanVerdict:
        if len(data) > self.max_scan_bytes:
            return ScanVerdict(ScanOutcome.ERROR, detail="file exceeds the configured maximum scan size")
        try:
            with self._connect() as sock:
                sock.sendall(b"zINSTREAM\0")
                for offset in range(0, len(data), _CHUNK):
                    chunk = data[offset : offset + _CHUNK]
                    sock.sendall(struct.pack("!L", len(chunk)) + chunk)
                sock.sendall(struct.pack("!L", 0))
                text = self._read_reply(sock)
        except OSError as exc:
            raise ScannerUnavailable(type(exc).__name__) from exc

        # Replies look like "stream: OK", "stream: Eicar-Signature FOUND", "INSTREAM size limit exceeded. ERROR".
        body = text.split(":", 1)[1].strip() if text.startswith("stream:") else text
        if body == "OK":
            return ScanVerdict(ScanOutcome.CLEAN)
        if body.endswith(" FOUND"):
            return ScanVerdict(ScanOutcome.INFECTED, signature=body.removesuffix(" FOUND").strip()[:200])
        if body.endswith("ERROR"):
            return ScanVerdict(ScanOutcome.ERROR, detail=body[:200])
        if not text:
            raise ScannerUnavailable("empty reply from scanner")
        return ScanVerdict(ScanOutcome.ERROR, detail=f"unexpected scanner reply: {body[:120]}")


def build_scanner(settings: Settings) -> MalwareScanner:
    if settings.scan_mode == "clamav":
        return ClamAVScanner(
            settings.clamav_host or "localhost", settings.clamav_port,
            connect_timeout=settings.clamav_connect_timeout_seconds,
            scan_timeout=settings.clamav_timeout_seconds,
            max_scan_bytes=settings.clamav_max_scan_bytes,
        )
    return DisabledScanner()


@lru_cache
def get_scanner() -> MalwareScanner:
    return build_scanner(get_settings())
