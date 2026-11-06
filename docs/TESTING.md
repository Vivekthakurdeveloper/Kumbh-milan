# Testing report

Command, from `backend`, against local Postgres (`TEST_DATABASE_URL` → `kumbh_setu_test`):

```bash
.venv/bin/python -m pytest -q --tb=line
```

## Result (2026-11-05, Phase 1 portal gap closure)

- Collected: 236
- Passed: 233
- Failed: 0
- Skipped: 3
- Errors: 0
- Duration: ~97 seconds
- Exit code: 0

Skips:

- `tests/test_ratelimit.py` ×2 — Redis not reachable (`docker compose up -d redis`, or set `TEST_REDIS_URL`)
- `tests/test_scanning.py::test_real_clamav_detects_eicar` — `CLAMAV_INTEGRATION_HOST` unset

Earlier recorded run (pre–Phase 1 portal): 222 passed, 1 skipped against a disposable Postgres on port 5434.

## What the suite covers

Auth, authorization, case APIs, workflow, match engine and match workflow, database constraints, media, scanning with a fake daemon, rate limits, platform settings, observability, geo, retention, notifications, readiness/export/MFA/reconciliation, end-to-end scenarios, and Phase 1 portal workflows (`test_phase1_portal.py`: login, missing/found registration with photo, search/notes/status, match queue + two-person review, directory, language validation, summary age/gender/location_id).

## Not executed

- Hosted GitHub Actions
- A real ClamAV daemon
- A real notification provider
- `load/cases.js`
- Re-run of `qa/api_smoke.py` against the two new endpoints (`GET /matches`, `GET /users/directory`)
