# Testing report

Command, from `backend`, against local Postgres (`TEST_DATABASE_URL` → `kumbh_setu_test`):

```bash
.venv/bin/python -m pytest -q --tb=line
.venv/bin/python -m ruff check app tests alembic
.venv/bin/python -m app.cli export-openapi --check   # regenerate with: python -m app.cli export-openapi
```

## Result (2026-11-21, registration profile and requested API contract)

- Local recorded run: **378 passed**, **1 skipped**.
- Hosted GitHub Actions on current `main` (`a60e8c3`): **379 passed**, **1 skipped**, with compile and `alembic check` also successful.
- The skip is `test_scanning.py::test_real_clamav_detects_eicar` without `CLAMAV_INTEGRATION_HOST`.
- Final post-generation auth/OpenAPI regression subset: **54 passed**
- Coverage added for PUBLIC registration validation/persistence/legacy login, `/auth/me`, normalized DB uniqueness, registration rate limiting, found-person role creation, coordinate concurrency, and match-photo authorization/TTL/expiry
- Alembic `0013` was tested through downgrade/upgrade, legacy-row preservation, duplicate-mobile rejection, and `alembic check`
- API documentation validator: **66 examples checked, 0 failed**
- Schema revision expected: **0013**

## Current worktree verification (2026-11-24, revision 0018)

- Full isolated PostgreSQL/PostGIS suite (fresh schema): **441 passed, 3 skipped** in 597 seconds.
- Skips: the two Redis-backed rate-limit tests (no Redis was running locally; CI provides one) and the real ClamAV EICAR test (`CLAMAV_INTEGRATION_HOST` unset).
- New coverage:
  - `test_grievance_retention.py`: grievance attachments (scan, replay, limit, privacy, download), grievance legal holds, and anonymisation, including the append-only trigger exception.
  - `test_key_rotation.py`: dual-read, dry run, resumable apply, idempotent re-run, validation of previous keys, and the rotation-only trigger exception.
  - `test_operations_modules.py`: realtime staff-notification events, PDF reports (two-page, empty, non-ASCII), and the WhatsApp inbound webhook (auth, idempotency, session reuse, locale, phone never stored).
- `alembic downgrade 0017` followed by `upgrade head` succeeds, and `alembic check` reports no drift.
- `ruff check app tests alembic` passes. Pre-existing unused imports were removed; migration files were not touched.
- `python -m app.cli export-openapi --check` passes: **165 paths / 196 operations**. The document produced without a `.env` file is identical.
- API documentation validator: **73 examples checked, 0 failed**.
- `docker compose --profile workers config` validates with the new `clamav` and `operations-worker` services. They were not started.

## Earlier worktree verification (2026-11-23)

- Full isolated PostgreSQL/PostGIS suite: **413 passed, 1 skipped** in 208 seconds.
- Skip: real ClamAV EICAR integration (`CLAMAV_INTEGRATION_HOST` unset).
- Targeted mobile/CORS/rate-limit suite: **40 passed**.
- Migrations rebuilt the disposable database through **0016**; `alembic current` reports `0016` and `alembic check` reports no drift.
- OpenAPI regenerated: **104 paths / 127 operations**.
- API documentation validator: **73 examples checked, 0 failed**.
- Application, tests, and Alembic revisions compile successfully; IDE diagnostics report no errors.
- Tests used fresh `kumbh-setu-test` containers/volumes. The corrupted Docker Desktop data retained from the old environment was not reset or deleted.

## Result (2026-11-11, PUBLIC accounts + blocked API gaps)

- **290 passed**, **1 skipped** (`test_scanning.py::test_real_clamav_detects_eicar` without `CLAMAV_INTEGRATION_HOST`)
- New coverage in `backend/tests/test_public_accounts.py`: PUBLIC register/login/me/refresh/logout, ownership/IDOR, forbidden staff routes, forgot/reset password, dashboard `from`/`to`, case `in_scope`/`verification_evidence`, browser-facing presign host
- Schema revision expected by that build: **0011**

## Result (2026-11-07, Phase 4 public portal)

- Collected: 279
- Passed: 278
- Failed: 0
- Skipped: 1
- Errors: 0
- Duration: ~155 seconds
- Exit code: 0

Skip:

- `tests/test_scanning.py::test_real_clamav_detects_eicar` — `CLAMAV_INTEGRATION_HOST` unset

Earlier recorded runs: Phase 1 portal gap closure 233 passed / 3 skipped (2026-11-05); Phase 3 mobile sync 263 passed / 1 skipped.

## What the suite covers

Auth, authorization, case APIs, workflow, match engine and match workflow, database constraints, media, scanning with a fake daemon, rate limits, platform settings, observability, geo, retention, notifications, readiness/export/MFA/reconciliation, end-to-end scenarios, portal workflows, mobile sync, and public portal (`test_phase4_public.py`: OTP, all four account-free case types, idempotent replay, staff verify/reject, status anti-enumeration, sightings, mock SMS/email/push, retry/backoff, push tokens, and public information).

## Not executed

- A real ClamAV daemon
- Live MSG91 / SMTP / Firebase delivery (adapters unit-tested via mock/disabled providers only)
- `load/cases.js`
- Full re-run of `qa/api_smoke.py` against Phase 4 public routes
