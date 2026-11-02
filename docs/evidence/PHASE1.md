# Phase 1 evidence

Recorded on 23 October 2026. Secrets, connection strings, and case contents are omitted.

## Runtime before changes

- Compose project containers `kumbh-setu-postgres-1`, `kumbh-setu-api-1`, and `kumbh-setu-objectstore-1` were already `Exited (137)`.
- Volumes `kumbh-setu_pgdata` and `kumbh-setu_objectdata` exist. They were not used as migration or test targets.
- Source migrations ran from `0001` through `0006` before this work and now include `0007`.
- There is no `.git` directory. No commit hash is claimed.
- The local virtualenv is Python 3.13. The Dockerfile base is `python:3.12-slim`.

## Backup

Directory: `.backups/20261023T090602Z` (gitignored).

- `pgdata.tar.gz`: 24,035,164 bytes, 1,872 archive members, contains `PG_VERSION` with value `16`.
- `objectdata.tar.gz`: 6,997 bytes, 76 archive members.
- `SHA256SUMS` was regenerated after the archives were written and `shasum -a 256 -c` reported both files `OK`.
- Restored into new volumes `kumbh-setu_pgdata_restore_20261023` and `kumbh-setu_objectdata_restore_20261023`.
- The restored Postgres data directory reported `PG_VERSION=16`. The object copy contained 64 files.
- `ops/restore-volume-copy.sh` exits 2 if the destination is `kumbh-setu_pgdata` or `kumbh-setu_objectdata`.

## Copy inspection and migration

A temporary server used only the restore volume and `127.0.0.1:5433`.

Before upgrade, `alembic_version` was `0001`. Public table count was 19. Row counts were cases 3, users 5, attachments 3, stations 2. No personal fields were selected.

`alembic upgrade head` on that copy then ran `0001 -> 0002 -> 0003 -> 0004 -> 0005 -> 0006 -> 0007` and exited 0.

`python -m app.cli schema-status` printed `expected=0007 current=0007`.

`alembic check` exited 0 with `No new upgrade operations detected`. It warned that SQLAlchemy does not recognise PostGIS `geography` on `cases.geo` and `locations.geog`. That warning is not a detected model drift.

The temporary server was stopped after the check. `kumbh-setu-postgres-1` was still `Exited (137)` and was not started.

## Readiness

`/ready` now requires database connectivity and Alembic revision `0007`. Object storage is required when S3 settings are present. Redis is required when `RATE_LIMIT_BACKEND=redis`. Worker heartbeats are required when `WORKERS_REQUIRED=true`. Failure responses use `ok`, `unavailable`, or `incompatible` and do not include exception text.

## Image

`docker compose build api` completed and tagged `kumbh-setu-api:latest`. The image manifest is `sha256:98d00e5b29046c63e92cead44c8b98726dbc799688f9525b4273a460c8b31f3a`.

`docker run --rm --entrypoint python kumbh-setu-api:latest` printed Python `3.12.15` and expected schema revision `0007`. The container was not attached to the operational database.

## Configuration

`.env.example` now includes Redis, scanning, notifications, retention, geo, workers, trusted proxies, metrics, and MFA. Placeholder values are not secrets. `README.md` no longer says Uvicorn uses `--proxy-headers`; the Dockerfile uses `--no-proxy-headers`.
