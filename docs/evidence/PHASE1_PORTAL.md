# Phase 1 portal — backend readiness evidence

Date: 2026-11-05  
Scope: backend gap closure for the Phase 1 Web Management Portal. **No frontend was built.**

## What already worked

The FastAPI backend already provided:

| Portal screen | Existing APIs |
|---|---|
| Login / logout / session | `POST /auth/login`, `/refresh`, `/logout`, `GET /auth/me` |
| Dashboard | `GET /dashboard/summary`; recent cases via `GET /cases?sort=-created_at&limit=10` |
| Register missing person | `POST /cases/missing-persons` + `POST /cases/{id}/attachments` |
| Register found person | `POST /cases/found-persons` + attachments |
| All cases | `GET /cases` (filters: `q`, `case_number`, `case_type`, `status`, pagination) |
| Case details | `GET /cases/{id}`, notes, history, attachments, matches, status, assign |
| Match review (per case) | `GET /cases/{id}/matches`, `POST …/review` (two-person rule) |
| Users (admin) | `GET /users`, create/patch/reset |
| Stations | `GET /stations` |

Matching is rule-based only. Scores must never be presented as facial recognition.

## Gaps closed in this work

| Gap | Change |
|---|---|
| G-15 | `language` on missing/found person schemas limited to 100 characters (matches DB). |
| G-14 + list fields | `CaseSummary` now includes `location_id`, `approximate_age`, `age_group`, `gender`. |
| G-1 | `GET /api/v1/matches` — cross-case review queue (`Page[MatchQueueItem]`). |
| G-3 | `GET /api/v1/users/directory?ids=` — display names for all roles. |

No database migration. No auth or password changes. Existing API contracts were extended only with optional response fields and two new GET endpoints.

## Portal field mapping (persons)

### Missing person registration → `POST /cases/missing-persons`

| Spec field | Backend field | Notes |
|---|---|---|
| Person name | `person_name` | Optional |
| Age | `approximate_age` / `age_group` | |
| Gender | `gender` | |
| Photograph | `POST /cases/{id}/attachments` (`media_type=PHOTO`) | After create |
| Clothing | `clothing_description` | |
| Identification marks | `identifying_marks` | |
| Last seen location | `last_seen_location` (+ optional `location_id` / coordinates) | |
| Last seen date/time | `last_seen_at` | |
| Language | `language` (max 100) | |
| Reporting person name / contact | **no structured field** | Put in write-only encrypted `contact_details` |

### Found person registration → `POST /cases/found-persons`

| Spec field | Backend field | Notes |
|---|---|---|
| Name | — | No name field on found persons |
| Approximate age | `approximate_age` / `age_group` | |
| Gender | `estimated_gender` | |
| Photograph | attachments | |
| Clothing / marks | `clothing_description`, `identifying_marks` | |
| Found location / time | `found_location`, `found_at` | |
| Current care location | `current_care_location` | |
| Finder information | **no structured field** | Use `contact_details` |

## Test report

Command (from `backend/`, with Postgres on `TEST_DATABASE_URL`):

```bash
.venv/bin/python -m pytest tests/test_phase1_portal.py -v
.venv/bin/python -m pytest -q --tb=line   # full suite
```

Phase 1 portal file: **13 passed**.

| Test | Covers |
|---|---|
| `test_workflow_missing_person_register_upload_and_view` | Workflow 1 |
| `test_workflow_found_person_register_upload_and_view` | Workflow 2 |
| `test_workflow_search_note_and_status` | Workflow 3 |
| `test_workflow_match_review_queue_two_person_rule` | Workflow 4 + audit |
| `test_language_over_100_chars_returns_422` | G-15 |
| `test_case_summary_includes_location_id_age_and_gender` | G-14 |
| `test_user_directory_returns_minimal_fields_for_all_roles` | G-3 |
| `test_match_queue_redacts_out_of_scope_side_for_helpdesk` | G-1 redaction |
| Invalid login, garbage token, out-of-station 404, bad upload, logout | Negative cases |

Full backend suite (2026-11-05): **233 passed, 3 skipped** (Redis ×2, ClamAV ×1). See [docs/TESTING.md](../TESTING.md).

## Remaining backend gaps (do not invent frontend workarounds as production features)

| Gap | Impact on Phase 1 portal |
|---|---|
| Structured reporter / finder fields | Only free-text `contact_details` exists |
| G-2 claimant / handover record | Reunification evidence stays free text |
| G-7 custody history for non-admins | Found-item custody chain not on case history |
| G-9 SMS/WhatsApp delivery | Family notifications stay `PENDING` |
| G-16 / G-17 CORS | Only if the UI is on a different origin than the API |
| G-8 dashboard depth | Counts only; no trends/activity feed |

## Setup

1. Copy `.env.example` → `.env` and set secrets (see [docs/DEPLOYMENT.md](../DEPLOYMENT.md)).
2. `docker compose up -d postgres objectstore redis`
3. Ensure `kumbh_setu_test` exists for pytest; Alembic upgrades run automatically in tests.
4. Run API: from `backend/`, `uvicorn app.main:app --reload` (or `docker compose up api`).
5. OpenAPI: `/docs` when `DOCS_ENABLED=true`; committed copy at [docs/api/openapi.json](../api/openapi.json).

## Definition of Done (backend for Phase 1)

1. Staff can log in — yes (`/auth/login`).
2. Dashboard real data — yes (`/dashboard/summary`).
3. Register missing / found — yes.
4. Photograph upload — yes.
5. Cases searchable and viewable — yes; summaries now include age/gender/location_id.
6. Case history — yes (`/history`); notes — yes.
7. Match suggestions reviewable — yes (`GET /matches` + per-case review).
8. Status / assignment for authorised officers — yes (existing).
9. Role and station permissions — enforced; covered by authorization + Phase 1 tests.
10. No mock data in API responses — yes.
11. No facial recognition claims — matching remains rule-based.
