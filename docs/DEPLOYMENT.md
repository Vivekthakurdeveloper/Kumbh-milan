# Deployment guide

No cloud account was used and no deployment was performed.

## Local

1. Copy `.env.example` to `.env` and replace placeholders. Do not commit `.env`.
2. `docker compose up -d postgres redis objectstore` starts dependencies. The API image does not migrate on startup.
3. Back up with `ops/backup-volumes.sh` before the first migration of a volume that already has data.
4. `docker compose --profile ops run --rm migrate` upgrades the database in that Compose project. Do not point it at the current operational volume until the `0001` to `0007` plan is approved. A copy has already been upgraded.
5. `docker compose up -d --build api` builds the Python 3.12 image and starts the API.
6. `docker compose --profile workers up -d scan-worker notifications-worker maintenance-worker` starts workers.

## Production prerequisites

- TLS terminates at the proxy. `TRUSTED_PROXIES` lists that proxy only.
- Managed PostgreSQL 16 with PostGIS, owner and app roles, and backups.
- Private object storage with the bucket blocked from public access.
- Redis for `RATE_LIMIT_BACKEND=redis`.
- ClamAV and `SCAN_MODE=clamav`.
- `DOCS_ENABLED=false`, a long `METRICS_TOKEN`, `WORKERS_REQUIRED=true`, and `NOTIFICATION_PROVIDER=disabled` until a provider is approved.
- `MFA_MODE` remains `disabled` until the MFA programme is approved.
- Secrets come from a secret manager. `CONTACT_ENCRYPTION_KEY` must be backed up before any data is written.

## CI

`.github/workflows/backend.yml` installs the backend on Python 3.12, compiles it, runs pytest against disposable PostGIS and Redis, and runs `alembic check`. The workspace has no Git remote, so this workflow has not been executed by a hosted runner.

## Rollback

Prefer restoring the tested backup into a new database over `alembic downgrade` once `0007` data exists. Application rollback is the previous image only when the schema is still compatible. `0007` is additive, so an older image that does not know the revision will fail `/ready` rather than write an incompatible schema.
