# Backend architecture

Kumbh Setu is one FastAPI process plus separately supervised worker processes. There is no frontend in this repository.

## Request path

`backend/app/main.py` mounts `/health`, `/ready`, `/metrics`, and `/api/v1`. Routers validate with Pydantic and call services. Services commit their own transactions. Repositories are used for case search; other services use SQLAlchemy directly.

## Modules

- `app/api/v1`: auth, stations, users, locations, four case types, shared case operations, audit, retention, dashboard, reports.
- `app/auth`: Argon2id passwords, JWT access tokens, refresh rotation, RBAC, station scope, optional TOTP step-up.
- `app/services`: cases, workflow, matching, geo, attachments, notifications, retention, dashboard, reports.
- `app/matching`: deterministic scoring. `ENGINE_VERSION` is stored with suggestions.
- `app/workers`: heartbeat writer used by CLI loops.
- `app/storage`: private S3-compatible objects and the ClamAV scanner client.
- `app/cli.py`: migrations are not run here; operational commands and worker loops are.

## Dependencies

- PostgreSQL 16 with PostGIS, through the owner role for migrations and `kumbh_app` for the API.
- Redis when the rate-limit backend is `redis`. Heartbeats themselves are rows in Postgres.
- SeaweedFS locally, or any private S3 API. The API does not serve object bytes.
- ClamAV only when `SCAN_MODE=clamav`.

## Workers

`scan-worker`, `notifications-worker`, and `maintenance-worker` are Compose services under profile `workers`. They are not started by `docker compose up`. The maintenance worker purges expired sessions and reports missing objects. It does not anonymise cases.

## Boundaries

- No SMS, WhatsApp, payment, or identity-provider integration is configured.
- `console` and `mock` notification providers are local only.
- TOTP is an application step-up factor, not an approved production MFA programme.
- Volunteer access is not a role.
