"""
Quarantine-first uploads against a fake clamd that speaks the real INSTREAM wire protocol.
An optional test against a real ClamAV daemon runs when CLAMAV_INTEGRATION_HOST is set.
"""

import os
import socket
import socketserver
import struct
import threading
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.common.time import utcnow
from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, CaseAttachment
from app.models.enums import ScanStatus
from app.services import attachments as attachment_service
from app.storage.scanner import ClamAVScanner, ScannerUnavailable, ScanOutcome, get_scanner
from tests.helpers import API, create_case

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 256
INFECTED_PNG = b"\x89PNG\r\n\x1a\n" + b"KUMBH-TEST-MALWARE" + b"\x00" * 64
EICAR = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


class _FakeClamd(socketserver.BaseRequestHandler):
    def handle(self):
        command = b""
        while not command.endswith(b"\0"):
            part = self.request.recv(1)
            if not part:
                return
            command += part
        if command == b"zPING\0":
            self.request.sendall(b"PONG\0")
            return
        data = b""
        while True:
            size = struct.unpack("!L", self._exact(4))[0]
            if size == 0:
                break
            data += self._exact(size)
        if b"KUMBH-TEST-MALWARE" in data:
            self.request.sendall(b"stream: Kumbh.Test.Signature FOUND\0")
        else:
            self.request.sendall(b"stream: OK\0")

    def _exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            part = self.request.recv(n - len(buf))
            if not part:
                raise ConnectionError
            buf += part
        return buf


@pytest.fixture
def clamd():
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _FakeClamd)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def _scanner(port: int, **kw) -> ClamAVScanner:
    return ClamAVScanner("127.0.0.1", port, connect_timeout=1, scan_timeout=2, max_scan_bytes=kw.get("max_bytes", 10**7))


def _dead_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def use_scanner():
    def install(scanner):
        app.dependency_overrides[get_scanner] = lambda: scanner

    yield install
    app.dependency_overrides.pop(get_scanner, None)


def _upload(client, actor, case_id, data=PNG):
    return client.post(
        f"{API}/cases/{case_id}/attachments", data={"media_type": "PHOTO"},
        files={"file": ("p.png", data, "image/png")}, headers=actor.headers,
    )


def _keys(storage, prefix: str) -> list[str]:
    return [o["Key"] for o in storage.client.list_objects_v2(Bucket=storage.bucket, Prefix=prefix).get("Contents", [])]


def _actions(attachment_id) -> list[str]:
    with SessionLocal() as db:
        return list(db.scalars(select(AuditEvent.action).where(AuditEvent.entity_id == attachment_id).order_by(AuditEvent.created_at)))


def test_clean_file_is_promoted_out_of_quarantine(client, actors, storage, clamd, use_scanner):
    use_scanner(_scanner(clamd))
    case = create_case(client, actors.helpdesk, "found-persons")
    r = _upload(client, actors.helpdesk, case["id"])
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["scan_status"] == "CLEAN" and body["downloadable"] is True
    assert _keys(storage, f"quarantine/{case['id']}/") == []
    assert len(_keys(storage, f"cases/{case['id']}/")) == 1
    dl = client.get(f"{API}/cases/{case['id']}/attachments/{body['id']}", headers=actors.police.headers)
    assert dl.status_code == 200
    assert _actions(body["id"]) == ["ATTACHMENT_UPLOADED", "ATTACHMENT_SCAN_CLEAN", "ATTACHMENT_ACCESSED"]


def test_infected_file_is_rejected_and_deleted(client, actors, storage, clamd, use_scanner):
    use_scanner(_scanner(clamd))
    case = create_case(client, actors.helpdesk, "found-persons")
    r = _upload(client, actors.helpdesk, case["id"], INFECTED_PNG)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "malware_detected"
    attachment_id = r.json()["error"]["details"]["attachment_id"]
    assert _keys(storage, f"quarantine/{case['id']}/") == [] and _keys(storage, f"cases/{case['id']}/") == []
    with SessionLocal() as db:
        row = db.get(CaseAttachment, attachment_id)
        assert row.scan_status == ScanStatus.REJECTED
        assert row.scan_signature == "Kumbh.Test.Signature" and row.object_purged_at is not None
    assert "ATTACHMENT_REJECTED" in _actions(attachment_id)
    dl = client.get(f"{API}/cases/{case['id']}/attachments/{attachment_id}", headers=actors.police.headers)
    assert dl.status_code == 409 and dl.json()["error"]["code"] == "attachment_not_available"


def test_invalid_signature_never_reaches_storage(client, actors, storage, clamd, use_scanner):
    use_scanner(_scanner(clamd))
    case = create_case(client, actors.helpdesk, "found-persons")
    r = _upload(client, actors.helpdesk, case["id"], b"MZ\x90\x00" + b"\x00" * 64)
    assert r.status_code == 415
    assert _keys(storage, f"quarantine/{case['id']}/") == []


def test_oversized_file_is_refused(client, actors, storage, clamd, use_scanner, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "max_photo_bytes", 100)
    use_scanner(_scanner(clamd))
    case = create_case(client, actors.helpdesk, "found-persons")
    r = _upload(client, actors.helpdesk, case["id"])
    assert r.status_code == 413
    assert _keys(storage, f"quarantine/{case['id']}/") == []


def test_scanner_unavailable_keeps_file_quarantined_and_undownloadable(client, actors, storage, use_scanner):
    use_scanner(_scanner(_dead_port()))
    case = create_case(client, actors.helpdesk, "found-persons")
    r = _upload(client, actors.helpdesk, case["id"])
    assert r.status_code == 202
    body = r.json()
    assert body["scan_status"] == "QUARANTINED" and body["downloadable"] is False
    assert len(_keys(storage, f"quarantine/{case['id']}/")) == 1
    assert _keys(storage, f"cases/{case['id']}/") == []
    dl = client.get(f"{API}/cases/{case['id']}/attachments/{body['id']}", headers=actors.police.headers)
    assert dl.status_code == 409 and dl.json()["error"]["code"] == "attachment_not_available"
    with SessionLocal() as db:
        row = db.get(CaseAttachment, body["id"])
        assert row.scan_attempts == 1 and row.next_scan_at > utcnow()
        assert row.last_scan_error.startswith("ScannerUnavailable")


def test_worker_retries_then_marks_clean(client, actors, storage, clamd, use_scanner):
    use_scanner(_scanner(_dead_port()))
    case = create_case(client, actors.helpdesk, "found-persons")
    attachment_id = _upload(client, actors.helpdesk, case["id"]).json()["id"]
    with SessionLocal() as db:
        assert attachment_service.due_scans(db, 10) == []  # backoff not yet elapsed
        db.execute(update(CaseAttachment).values(next_scan_at=utcnow() - timedelta(seconds=1)))
        db.commit()
        due = attachment_service.due_scans(db, 10)
        assert [str(i) for i in due] == [attachment_id]
        assert attachment_service.process_scan(db, storage, _scanner(clamd), due[0]) == ScanStatus.CLEAN
    assert _keys(storage, f"quarantine/{case['id']}/") == []
    dl = client.get(f"{API}/cases/{case['id']}/attachments/{attachment_id}", headers=actors.police.headers)
    assert dl.status_code == 200


def test_retries_exhausted_marks_scan_failed_never_clean(client, actors, storage, use_scanner, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "scan_max_attempts", 2)
    dead = _scanner(_dead_port())
    use_scanner(dead)
    case = create_case(client, actors.helpdesk, "found-persons")
    attachment_id = _upload(client, actors.helpdesk, case["id"]).json()["id"]
    with SessionLocal() as db:
        db.execute(update(CaseAttachment).values(next_scan_at=utcnow()))
        db.commit()
        assert attachment_service.process_scan(db, storage, dead, attachment_id) == ScanStatus.SCAN_FAILED
        row = db.get(CaseAttachment, attachment_id, populate_existing=True)
        assert row.scan_attempts == 2 and row.next_scan_at is None
    assert "ATTACHMENT_SCAN_FAILED" in _actions(attachment_id)
    dl = client.get(f"{API}/cases/{case['id']}/attachments/{attachment_id}", headers=actors.police.headers)
    assert dl.status_code == 409


def test_stale_scanning_rows_are_released(client, actors, storage, use_scanner):
    use_scanner(_scanner(_dead_port()))
    case = create_case(client, actors.helpdesk, "found-persons")
    attachment_id = _upload(client, actors.helpdesk, case["id"]).json()["id"]
    with SessionLocal() as db:
        db.execute(
            update(CaseAttachment).values(scan_status=ScanStatus.SCANNING, scan_started_at=utcnow() - timedelta(hours=1))
        )
        db.commit()
        assert attachment_service.release_stale_scans(db) == 1
        assert db.get(CaseAttachment, attachment_id, populate_existing=True).scan_status == ScanStatus.QUARANTINED


def test_concurrent_workers_scan_once(client, actors, storage, clamd, use_scanner):
    use_scanner(_scanner(_dead_port()))
    case = create_case(client, actors.helpdesk, "found-persons")
    attachment_id = _upload(client, actors.helpdesk, case["id"]).json()["id"]
    with SessionLocal() as db:
        db.execute(update(CaseAttachment).values(next_scan_at=utcnow()))
        db.commit()
    results: list[ScanStatus] = []
    barrier = threading.Barrier(4)

    def work():
        barrier.wait()
        with SessionLocal() as db:
            results.append(attachment_service.process_scan(db, storage, _scanner(clamd), attachment_id))

    threads = [threading.Thread(target=work) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert ScanStatus.CLEAN in results
    assert _actions(attachment_id).count("ATTACHMENT_SCAN_CLEAN") == 1


def test_unauthorized_download_is_refused(client, actors, use_scanner, clamd):
    use_scanner(_scanner(clamd))
    case = create_case(client, actors.helpdesk, "found-persons")
    attachment_id = _upload(client, actors.helpdesk, case["id"]).json()["id"]
    url = f"{API}/cases/{case['id']}/attachments/{attachment_id}"
    assert client.get(url).status_code == 401
    assert client.get(url, headers=actors.police_b.headers).status_code == 404


def test_skipped_dev_files_are_not_downloadable_in_production(client, actors, monkeypatch):
    from app.config import get_settings

    case = create_case(client, actors.helpdesk, "found-persons")
    body = _upload(client, actors.helpdesk, case["id"]).json()
    assert body["scan_status"] == "SKIPPED_DEV"
    monkeypatch.setattr(get_settings(), "app_env", "production")
    r = client.get(f"{API}/cases/{case['id']}/attachments/{body['id']}", headers=actors.police.headers)
    assert r.status_code == 409


def test_clamav_adapter_protocol(clamd):
    scanner = _scanner(clamd)
    assert scanner.ping() and scanner.health() == "ok"
    assert scanner.scan(PNG).outcome is ScanOutcome.CLEAN
    infected = scanner.scan(INFECTED_PNG)
    assert infected.outcome is ScanOutcome.INFECTED and infected.signature == "Kumbh.Test.Signature"
    big = _scanner(clamd, max_bytes=10).scan(PNG)
    assert big.outcome is ScanOutcome.ERROR
    with pytest.raises(ScannerUnavailable):
        _scanner(_dead_port()).scan(PNG)
    assert _scanner(_dead_port()).health() == "unavailable"


@pytest.mark.skipif(not os.environ.get("CLAMAV_INTEGRATION_HOST"), reason="set CLAMAV_INTEGRATION_HOST to test a real clamd")
def test_real_clamav_detects_eicar():
    host = os.environ["CLAMAV_INTEGRATION_HOST"]
    port = int(os.environ.get("CLAMAV_INTEGRATION_PORT", "3310"))
    scanner = ClamAVScanner(host, port, connect_timeout=3, scan_timeout=60, max_scan_bytes=10**7)
    assert scanner.ping()
    assert scanner.scan(PNG).outcome is ScanOutcome.CLEAN
    verdict = scanner.scan(EICAR)
    assert verdict.outcome is ScanOutcome.INFECTED and "Eicar" in verdict.signature
