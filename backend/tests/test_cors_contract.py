from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

ALLOWED_ORIGIN = "https://console.kumbh.example.org"


def _client(monkeypatch) -> TestClient:
    settings = Settings(cors_origins=[ALLOWED_ORIGIN])
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    return TestClient(create_app())


def _header_values(response, name: str) -> set[str]:
    return {value.strip().lower() for value in response.headers[name].split(",")}


def test_cors_allows_step_up_header_for_approved_origin(monkeypatch):
    with _client(monkeypatch) as client:
        response = client.options(
            "/api/v1/users/00000000-0000-0000-0000-000000000000/reset-password",
            headers={
                "Origin": ALLOWED_ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type,x-step-up-code",
            },
        )

    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == ALLOWED_ORIGIN
    assert "x-step-up-code" in _header_values(response, "Access-Control-Allow-Headers")


def test_cors_exposes_export_metadata_to_approved_origin(monkeypatch):
    with _client(monkeypatch) as client:
        response = client.get("/health", headers={"Origin": ALLOWED_ORIGIN})

    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == ALLOWED_ORIGIN
    exposed = _header_values(response, "Access-Control-Expose-Headers")
    assert {
        "content-disposition",
        "x-export-rows",
        "x-export-truncated",
        "x-request-id",
    } <= exposed


def test_cors_allows_resumable_upload_put_for_approved_origin(monkeypatch):
    with _client(monkeypatch) as client:
        response = client.options(
            "/api/v1/sync/attachments/sessions/00000000-0000-0000-0000-000000000000/content",
            headers={
                "Origin": ALLOWED_ORIGIN,
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

    assert response.status_code == 200
    assert "put" in _header_values(response, "Access-Control-Allow-Methods")


def test_cors_rejects_unapproved_origin(monkeypatch):
    with _client(monkeypatch) as client:
        response = client.options(
            "/api/v1/reports/cases.csv",
            headers={
                "Origin": "https://attacker.example.org",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 400
    assert "Access-Control-Allow-Origin" not in response.headers
