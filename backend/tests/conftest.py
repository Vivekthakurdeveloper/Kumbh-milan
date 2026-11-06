"""
Tests run against a dedicated PostgreSQL database (TEST_DATABASE_URL) and an in-memory S3 (moto).
The schema is rebuilt with Alembic once per session and every table is truncated between tests.
"""

import os
from pathlib import Path
from urllib.parse import urlparse

import pytest


def _read_dotenv() -> dict[str, str]:
    values: dict[str, str] = {}
    for candidate in (Path(__file__).resolve().parents[2] / ".env", Path(__file__).resolve().parents[1] / ".env"):
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    values.setdefault(key.strip(), value.strip())
    return values


TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or _read_dotenv().get("TEST_DATABASE_URL")
if not TEST_DATABASE_URL:
    raise RuntimeError("Set TEST_DATABASE_URL to a dedicated, disposable PostgreSQL database")
_parsed_test_url = urlparse(TEST_DATABASE_URL)
_test_db_name = _parsed_test_url.path.rsplit("/", 1)[-1]
_test_db_host = (_parsed_test_url.hostname or "").lower()
_LOCAL_TEST_HOSTS = {"localhost", "127.0.0.1", "::1", "postgres", "host.docker.internal"}
if not _test_db_name.endswith("_test") or _test_db_name in {"kumbh_setu", "postgres", "template0", "template1"}:
    raise RuntimeError("Refusing to run: TEST_DATABASE_URL must name a disposable database ending in '_test'")
if _test_db_host not in _LOCAL_TEST_HOSTS:
    raise RuntimeError(f"Refusing to run tests against non-local database host {_test_db_host}")

os.environ.update(
    {
        "APP_ENV": "test",
        "DATABASE_URL": TEST_DATABASE_URL,
        "KUMBH_APP_DB_ROLE": "",
        "S3_ENDPOINT_URL": "",
        "S3_PUBLIC_ENDPOINT_URL": "",
        "S3_REGION": "us-east-1",
        "S3_BUCKET": "kumbh-test-media",
        "S3_ACCESS_KEY_ID": "testing",
        "S3_SECRET_ACCESS_KEY": "testing",
        "S3_SERVER_SIDE_ENCRYPTION": "AES256",
        "CLAMAV_HOST": "",
        "SCAN_MODE": "disabled",
        "MATCHING_AUTO_ENABLED": "false",
        "FACE_MATCHING_ENABLED": "false",
        "FACE_MODEL_DIR": str(Path(__file__).resolve().parents[1] / "models" / "faces"),
        "FACE_EMBEDDING_ENCRYPTION_KEY": "lMn5f0L2tE3q1o6lqk8Hh1pTq3n3Zx0n6dY2M0fN2Vo=",
        "RATE_LIMIT_BACKEND": "memory",
        "REDIS_URL": "",
        "METRICS_TOKEN": "",
        "TRUSTED_PROXIES": "[]",
        "RATE_LIMIT_PER_MINUTE": "100000",
        "RATE_LIMIT_LOGIN_PER_MINUTE": "100000",
        "RATE_LIMIT_REFRESH_PER_MINUTE": "100000",
        "RATE_LIMIT_PASSWORD_PER_HOUR": "100000",
        "RATE_LIMIT_UPLOAD_PER_MINUTE": "100000",
        "RATE_LIMIT_CASE_CREATE_PER_MINUTE": "100000",
        "RATE_LIMIT_SEARCH_PER_MINUTE": "100000",
        "SYNC_PULL_LAG_SECONDS": "0",
        "SYNC_MAX_BATCH": "50",
        "WORKERS_REQUIRED": "false",
        "MFA_MODE": "disabled",
        "LOGIN_MAX_FAILURES": "3",
        "JWT_SECRET": "test-only-secret-value-that-is-long-enough-123456",
        "CONTACT_ENCRYPTION_KEY": "lMn5f0L2tE3q1o6lqk8Hh1pTq3n3Zx0n6dY2M0fN2Vo=",
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
    }
)

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from moto import mock_aws  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal, engine  # noqa: E402
from app.database.base import Base  # noqa: E402
from app.main import app  # noqa: E402
from app.storage import ObjectStorage, get_storage  # noqa: E402
from tests.helpers import Actors, make_actors  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def migrated_database():
    lock = engine.connect()
    if not lock.execute(text("SELECT pg_try_advisory_lock(872341)")).scalar():
        lock.close()
        raise RuntimeError("Another pytest process is using this test database; run one suite at a time")
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.attributes["database_url"] = TEST_DATABASE_URL
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield
    lock.execute(text("SELECT pg_advisory_unlock(872341)"))
    lock.close()
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_tables(migrated_database):
    yield
    tables = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture(scope="session")
def storage():
    from app.config import get_settings

    with mock_aws():
        store = ObjectStorage(get_settings())
        store.ensure_bucket()
        yield store


@pytest.fixture
def client(storage):
    app.dependency_overrides[get_storage] = lambda: storage
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def actors(client) -> Actors:
    return make_actors(client)
