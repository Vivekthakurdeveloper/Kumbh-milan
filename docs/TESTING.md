# Testing report

Command, from `backend`, against a new Postgres 16/PostGIS container on `127.0.0.1:5434` and database `kumbh_setu_test`:

```bash
TEST_DATABASE_URL=postgresql+psycopg://kumbh_owner:***@127.0.0.1:5434/kumbh_setu_test \
  .venv/bin/python -m pytest -q --tb=line
```

The operational server on port 5432 was not started. The virtualenv used for this run is Python 3.13. The image build uses Python 3.12.

## Result

- Collected: 223
- Passed: 222
- Failed: 0
- Skipped: 1
- Errors: 0
- Duration: 157.54 seconds
- Exit code: 0

The skip is `tests/test_scanning.py::test_real_clamav_detects_eicar` because `CLAMAV_INTEGRATION_HOST` is unset. Redis rate-limit tests were not skipped; the already-stopped Compose Redis container was started and does not use the Postgres volume.

An earlier run of the same command reported 220 passed and 2 failed. Those failures were the safeguarding tests expecting `safeguarding_evidence_required` while the generic resolver returned `verification_required` first. The order was corrected and the full command above was repeated.

## What the suite covers

Auth, authorization, case APIs, workflow, match engine and match workflow, database constraints, media, scanning with a fake daemon, rate limits, platform settings, observability, geo, retention, notifications, the new readiness/export/MFA/reconciliation tests, and five end-to-end scenarios.

`alembic check` was run against the restored copy on port 5433, not only against the empty test database. It exited 0.

## Not executed

- Hosted GitHub Actions
- A real ClamAV daemon
- A real notification provider
- `load/cases.js`
- A restore of a logical dump into a second managed database. The volume-archive restore and upgrade of the copy was executed and is recorded in `docs/evidence/PHASE1.md`.
