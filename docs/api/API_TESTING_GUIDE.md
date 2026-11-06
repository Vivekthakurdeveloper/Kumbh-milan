# API testing and verification guide

How to verify the API contracts described in this folder, what has already been tested, and what was and was not executed during this documentation exercise.

**Safety rules:** run tests only against a disposable local stack or a dedicated test database. The pytest fixtures refuse to run unless `TEST_DATABASE_URL` points at a local host and a database whose name ends in `_test` (`backend/tests/conftest.py`). Never run the QA harness or write tests against staging or production data. Never paste real tokens, passwords, or personal data into test fixtures, tickets, or Postman collections.

## 1. Verification performed for this documentation

| Check | Command | Result | Date |
|---|---|---|---|
| OpenAPI generated from source | `APP_ENV=local DOCS_ENABLED=true .venv/bin/python -c "…json.dump(app.openapi(), …)"` (from `backend/`) | 63 paths, 83 operations → [openapi.json](./openapi.json) | 2026-11-05 |
| Documentation examples validated against backend Pydantic models | `backend/.venv/bin/python docs/api/tools/validate_examples.py` (from repo root) | **57 examples checked, 0 failed** | 2026-11-05 |
| Validator negative check (missing required field, naive datetime, future datetime, invented response field) | ad-hoc script calling the validator functions | All four rejected | 2026-10-29 |
| Hand-written TypeScript interfaces in the integration guide compared with Pydantic model fields and enum values | ad-hoc script | No missing or extra fields; all enum value sets equal | 2026-10-29 |
| Schema max lengths vs database column lengths | ad-hoc script over SQLAlchemy metadata | **No mismatch** (`language` fixed; was gap G-15) | 2026-11-05 |
| Database-free unit tests | `APP_ENV=test JWT_SECRET=<any 32+ char test value> .venv/bin/python -m pytest --noconftest -q tests/test_matching_engine.py tests/test_ratelimit.py` | **17 passed, 2 skipped** (Redis not reachable) | 2026-10-29 |
| Full pytest suite (Phase 1 portal gaps) | `backend/.venv/bin/python -m pytest -q --tb=line` | **233 passed, 3 skipped** | 2026-11-05 |

## 2. Existing test evidence

| Suite | Location | Last recorded result | Source |
|---|---|---|---|
| pytest (unit, integration, e2e, Phase 1 portal) | `backend/tests/` (19 test files) | 233 passed, 3 skipped (Redis ×2, ClamAV ×1) | `docs/TESTING.md`, `docs/evidence/PHASE1_PORTAL.md` |
| Live HTTP QA harness | `qa/api_smoke.py` | 144/144 checks passed on 2026-10-23 (pre–Phase 1 portal endpoints) | `docs/evidence/QA_LOCAL_RUN.md` |

Test files by area: `test_auth.py`, `test_authorization.py`, `test_cases_api.py`, `test_workflow.py`, `test_match_workflow.py`, `test_matching.py`, `test_matching_engine.py`, `test_media.py`, `test_scanning.py`, `test_geo.py`, `test_notifications.py`, `test_retention.py`, `test_ratelimit.py`, `test_observability.py`, `test_platform.py`, `test_database.py`, `test_phase_completion.py`, `test_phase1_portal.py`, `test_e2e_scenarios.py`.

Recorded as not executed by the project: hosted CI, real ClamAV, real notification provider, load test `load/cases.js`.

## 3. Running the suites locally

### 3.1 pytest (needs PostgreSQL 16 + PostGIS; Redis optional)

```bash
docker compose up -d postgres redis
# create a database whose name ends in _test, e.g. kumbh_setu_test
cd backend
TEST_DATABASE_URL=postgresql+psycopg://<user>:<password>@127.0.0.1:<port>/kumbh_setu_test \
  .venv/bin/python -m pytest -q --tb=line
```

Without a database, only `tests/test_matching_engine.py` and `tests/test_ratelimit.py` run, with `--noconftest` as in §1.

### 3.2 Live HTTP harness (disposable stack)

Follow `docs/evidence/QA_LOCAL_RUN.md`: start a separate Compose project (`-p kumbh-setu-qa`), migrate, create the admin account the harness expects (username and password are defined in `qa/api_smoke.py`) with `python -m app.cli create-admin`, run `python3 qa/api_smoke.py http://127.0.0.1:8000`, then `docker compose -p kumbh-setu-qa down -v`.

### 3.3 Documentation checks (no services needed)

```bash
backend/.venv/bin/python docs/api/tools/validate_examples.py
```

Re-run whenever a schema or an example changes. To regenerate the OpenAPI document after backend changes (from `backend/`):

```bash
APP_ENV=local DOCS_ENABLED=true .venv/bin/python -c \
  "import json; from app.main import app; json.dump(app.openapi(), open('../docs/api/openapi.json','w'), indent=2, ensure_ascii=False)"
```

## 4. Manual test cases

Use a local stack, an admin created with `create-admin`, then a police officer and a help-desk user created through `POST /users`. `$API` is `http://localhost:8000/api/v1`; `$TOKEN` is an access token from login (sample placeholders, not real values).

```bash
TOKEN=$(curl -s -X POST "$API/auth/login" -H 'Content-Type: application/json' \
  -d '{"username":"<officer>","password":"<password>"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -s "$API/auth/me" -H "Authorization: Bearer $TOKEN"
```

### 4.1 Positive flows

| # | Steps | Expected |
|---|---|---|
| P-1 | Login, `GET /auth/me` | `200`; `permissions` matches the role matrix in the catalog §12 |
| P-2 | `POST /cases/missing-persons` with the sample body from the reference §6.3 and an `Idempotency-Key` | `201`; `case_number` `KSLF-MP-2026-…`; `status: OPEN`; `version: 1` |
| P-3 | Repeat P-2 with the same key and body | `201` with the same `id`; header `Idempotent-Replayed: true` |
| P-4 | `POST /cases/found-persons` with matching attributes | `201`; `GET /cases/{mp}/matches` shows an `AUTOMATIC` `PROPOSED` match with `reasons` |
| P-5 | Officer confirms the match with `evidence` | `200`; both cases `MATCH_FOUND` |
| P-6 | Status `RESOLVED` with `match_id` | `200`; history shows the transition |
| P-7 | Found item: transfer (with `storage_location`), release (with `recipient_acknowledgement`), resolve | Each `200`; resolve before release → `409 custody_not_released` |
| P-8 | Upload a JPEG photo | `201` (scanner disabled locally) or `202`; `GET …/attachments/{id}` returns a URL that downloads the file within 300 s |
| P-9 | `GET /dashboard/summary`, `GET /reports/cases.csv?created_from=…&created_to=…` | Counts consistent with created data; CSV with 6 columns |

### 4.2 Validation

| # | Request | Expected |
|---|---|---|
| V-1 | Lost item without `item_category` | `422 validation_failed`, `loc: ["body","item_category"]` |
| V-2 | Unknown field `colour` | `422`, `type: extra_forbidden` |
| V-3 | `last_seen_at` without offset, or in the future | `422` |
| V-4 | `latitude` without `longitude` | `422` with model-level `msg` |
| V-5 | Coordinates outside the Mela area | `422 coordinates_out_of_area` |
| V-6 | `limit=101` | `422` |
| V-7 | `language` of 150 characters | `422 validation_failed` with `loc` containing `language` |
| V-9 | `GET /matches` after proposing a match | `200`; `total` equals dashboard `pending_match_reviews` |
| V-10 | `GET /users/directory?ids=<assignee>&ids=<reporter>` as help desk | `200`; only `id`, `full_name`, `role`, `station_id` |
| V-8 | Weak password on `POST /users` | `422 weak_password` |

### 4.3 Authentication and authorization

| # | Scenario | Expected |
|---|---|---|
| A-1 | No `Authorization` header | `401`, `WWW-Authenticate: Bearer` |
| A-2 | Expired access token | `401 token_expired`; refresh succeeds |
| A-3 | Refresh twice with the same refresh token | Second call `401`; the session is revoked and the newest refresh token also fails |
| A-4 | 5 wrong passwords | `429 account_temporarily_locked` |
| A-5 | Help desk: `POST …/assign`, `POST …/contact/reveal`, `GET /audit-events`, `GET /users` | `403 permission_denied` |
| A-6 | Help desk: `GET /cases/{id}` of another station | `404 case_not_found` |
| A-7 | Help desk: `GET /cases?all_stations=true` | `403` |
| A-8 | Police: status `CLOSED` on a case at another station not linked to them | `404` |
| A-9 | Proposer of a manual match tries to confirm it | `403 separation_of_duties` |
| A-10 | Admin deactivates self | `403 self_lockout` |

### 4.4 Workflow and state transitions

| # | Scenario | Expected |
|---|---|---|
| W-1 | `OPEN` → `RESOLVED` | `409 invalid_status_transition` |
| W-2 | Resolve without match or evidence | `422 verification_required` |
| W-3 | Resolve a safeguarding-flagged found person with only `match_id` | `422 safeguarding_evidence_required` |
| W-4 | PATCH with a stale `expected_version` | `409 case_changed`, `details.current_version` |
| W-5 | PATCH a `CLOSED` case | `409 case_closed` |
| W-6 | Assign the same officer again | `409 already_assigned` |
| W-7 | Custody `RELEASED` → `IN_CUSTODY` | `409 invalid_custody_transition` |
| W-8 | Revoke a confirmed match | `200`; cases leave `MATCH_FOUND` |
| W-9 | Same `Idempotency-Key`, different body | `409 idempotency_key_reused` |

### 4.5 Uploads

| # | Scenario | Expected |
|---|---|---|
| U-1 | HEIC photo | `415 unsupported_media_type` |
| U-2 | PNG bytes sent as `media_type=AUDIO` | `415 media_type_mismatch` |
| U-3 | 11 MB photo | `413` |
| U-4 | Empty file | `422 empty_file` |
| U-5 | Upload to a closed case | `409 case_closed` |
| U-6 | Download a quarantined attachment | `409 attachment_not_available` |
| U-7 | `DELETE` without JSON body | `422`; with `{"reason": "…"}` → `204` |

### 4.6 Search, pagination, map

| # | Scenario | Expected |
|---|---|---|
| S-1 | `GET /cases?q=<name fragment>` | Matches across types; `total` correct |
| S-2 | `status=OPEN&status=ASSIGNED` | Both statuses only |
| S-3 | `limit=2&offset=2` | Items 3–4; `total` unchanged |
| S-4 | `GET /cases/map` with oversized bbox | `422 bbox_too_large` |
| S-5 | `GET /cases/nearby` with huge radius | `422 radius_too_large` |

## 5. Postman / Newman

No collection exists in the repository. To create one: import [openapi.json](./openapi.json) into Postman (Import → OpenAPI 3.1), set a collection variable `baseUrl`, add a pre-request login script that stores `access_token` in a **local, non-synced** variable, and add tests from §4. Run with:

```bash
npx newman run kumbh-setu.postman_collection.json -e local.postman_environment.json
```

Do not commit environment files that contain passwords or tokens.

## 6. Frontend contract tests

See [FRONTEND_INTEGRATION_GUIDE.md §13](./FRONTEND_INTEGRATION_GUIDE.md): generate types from `openapi.json`, mock with MSW using the validated examples, and cover token refresh, `409 case_changed`, `422` mapping, `202` uploads, dashboard missing keys, and redacted match rows.
