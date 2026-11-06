# Phase 3 delivery report — Mobile backend and offline synchronization

Backend only. No Android, iOS, or web UI work.

## 1. Existing functionality reused

- JWT login / refresh / me (`/api/v1/auth/*`)
- Role permissions and station-scoped `case_visibility_clause`
- Type-specific case create/list/read/update, unified search, notes, attachments
- Optimistic locking via `Case.version` / `expected_version` → 409 `case_changed`
- `Idempotency-Key` helpers (`run_idempotent`, `run_idempotent_operation`)
- Quarantine-first private media storage, malware scan, audited presigned downloads
- Append-only audit writer and case number allocation

## 2. New APIs implemented

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/sync/push` | Batch offline CREATE_CASE / UPDATE_CASE / ADD_NOTE |
| GET | `/api/v1/sync/pull` | Incremental download with opaque cursor |
| GET | `/api/v1/sync/status` | Last successful sync + client-reported backlog + server cursor |

Extended existing write paths:

- Optional sync fields on case create and note create
- `Idempotency-Key` on `PATCH /cases/{type}/{id}`
- `Idempotency-Key` plus `device_id` / `client_record_id` form fields on attachment upload

## 3. Database migrations added

- [backend/alembic/versions/20261105_0009_mobile_sync.py](../../backend/alembic/versions/20261105_0009_mobile_sync.py)
- Additive columns on `cases`, `case_notes`, `case_attachments`
- `cases.sync_changed_at` (backfilled from `updated_at`) + triggers so notes/attachments bump the pull cursor without bumping `version`
- Partial unique indexes on `(owner_user, device_id, client_record_id)`
- `sync_devices` table
- `EXPECTED_SCHEMA_REVISION = "0009"`

## 4. Files and services modified / added

Added:

- `backend/app/models/sync.py`
- `backend/app/schemas/sync.py`
- `backend/app/services/sync.py`
- `backend/app/api/v1/sync.py`
- `backend/tests/test_sync.py`
- `docs/MOBILE_SYNC.md`

Modified:

- Models: `case.py`, `workflow.py`, `__init__.py`
- Services: `cases.py`, `workflow.py`, `attachments.py`
- API: `cases.py`, `router.py`
- `audit/writer.py`, `ratelimit/policies.py`, `config/settings.py`, `database/revision.py`
- `tests/conftest.py`, `.env.example`, API docs inventory / frontend guide

## 5. Tests executed and results

```text
cd backend && python -m pytest tests/ -q
263 passed, 1 skipped
```

`tests/test_sync.py` covers:

1. Offline missing-person registration  
2. Offline found-person registration  
3. Lost-item synchronization  
4. Found-item synchronization  
5. Duplicate sync request  
6. Repeated / concurrent retry without duplicates  
7. Successful synchronization + status + audit  
8. Failed validation (other records still saved)  
9. Conflicting updates and resolution  
10. Unauthorized station push / pull isolation  
11. Photograph upload, association, retry, bad type/size  
12. Pull pagination and note-driven cursor bump  

## 6. Remaining gaps

- No deletion / scope-revocation tombstones on pull
- Non-sync `Idempotency-Key` still TTL-limited (24 h); durable offline identity is `client_record_id`
- Pull returns case envelopes only; notes/attachments are fetched via existing endpoints
- Photographs are not embedded in `/sync/push`
- No separate admin “device registration” workflow (implicit on first sync)

## 7. API request and response examples

See [docs/MOBILE_SYNC.md](../MOBILE_SYNC.md).

## 8. Deployment and migration instructions

1. Deploy the new API image / code (includes revision expectation `0009`).
2. Run migrations with the owner role against the existing database:

   ```bash
   cd backend
   alembic upgrade head
   ```

3. Confirm readiness:

   ```bash
   curl -sS http://localhost:8000/ready
   # checks.schema must be "ok"
   ```

4. Optional env (defaults are fine for most deployments):

   ```bash
   SYNC_MAX_BATCH=50
   SYNC_PULL_LAG_SECONDS=5
   ```

5. Mobile clients: login as today, then use `/sync/push`, `/sync/pull`, `/sync/status`, and existing attachment upload with `client_record_id` for photo retries.

Downgrade (dev/test only; do not downgrade production with live sync data):

```bash
alembic downgrade 0008
```
