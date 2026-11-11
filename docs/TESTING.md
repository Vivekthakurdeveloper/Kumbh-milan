# Testing report

Command, from `backend`, against local Postgres (`TEST_DATABASE_URL` → `kumbh_setu_test`):

```bash
.venv/bin/python -m pytest -q --tb=line
```

## Result (2026-11-11, PUBLIC accounts + blocked API gaps)

- **290 passed**, **1 skipped** (`test_scanning.py::test_real_clamav_detects_eicar` without `CLAMAV_INTEGRATION_HOST`)
- New coverage in `backend/tests/test_public_accounts.py`: PUBLIC register/login/me/refresh/logout, ownership/IDOR, forbidden staff routes, forgot/reset password, dashboard `from`/`to`, case `in_scope`/`verification_evidence`, browser-facing presign host
- Schema revision expected: **0011**

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

Auth, authorization, case APIs, workflow, match engine and match workflow, database constraints, media, scanning with a fake daemon, rate limits, platform settings, observability, geo, retention, notifications, readiness/export/MFA/reconciliation, end-to-end scenarios, Phase 1 portal workflows, Phase 3 mobile sync, and Phase 4 public portal (`test_phase4_public.py`: OTP, public missing-person intake + idempotency, status anti-enumeration, sightings, mock SMS/email/push, retry/backoff, push tokens, public information, staff public-review privacy).

## Not executed

- Hosted GitHub Actions
- A real ClamAV daemon
- Live MSG91 / SMTP / Firebase delivery (adapters unit-tested via mock/disabled providers only)
- `load/cases.js`
- Full re-run of `qa/api_smoke.py` against Phase 4 public routes
