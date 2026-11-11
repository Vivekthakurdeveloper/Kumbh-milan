# API endpoint catalog

Complete inventory of every HTTP operation in the FastAPI application (`backend/app/main.py`). The list was produced by importing the app and reading its generated OpenAPI document ([openapi.json](./openapi.json)). One more route, `GET /metrics`, is excluded from the schema and listed separately. Phase 4 adds the public citizen portal, sightings staff APIs, public-information admin APIs, and device push-token routes.

Detailed request and response contracts are in [API_REQUEST_RESPONSE_REFERENCE.md](./API_REQUEST_RESPONSE_REFERENCE.md).

## How to read this catalog

**Roles** in code: `ADMIN`, `POLICE_OFFICER` ("Police"), `HELP_DESK_OPERATOR` ("Help desk"), and `PUBLIC` (citizen self-registration). "All roles" means any authenticated active user unless a row says otherwise. Staff permission strings are defined in `backend/app/auth/permissions.py` (matrix in section 12). `PUBLIC` returns `permissions: []` from `/auth/me`; allowed case routes are gated by a default-deny allowlist plus reporter ownership.

**Auth**: "Bearer" means `Authorization: Bearer <access_token>` is required. Missing or invalid credentials return `401`.

**Scope**: case endpoints apply row-level visibility. A case outside the caller's scope returns `404 case_not_found`, never `403`, so existence is not leaked. PUBLIC callers only see cases where `reported_by_user_id` is themselves.

**Status** (implementation status, per the classification requested for this documentation):

| Label | Meaning |
|---|---|
| **Implemented — tested** | Code inspected; route present in the generated OpenAPI; exercised by an automated test in `backend/tests/` and/or the live HTTP harness `qa/api_smoke.py`. Latest full pytest: `docs/TESTING.md` (290 passed / 1 skipped). Earlier HTTP harness: `docs/evidence/QA_LOCAL_RUN.md` (144/144). |
| **Implemented — no test found** | Code inspected and route present, but no test exercising it was found. |
| **Partial** | Endpoint exists but does not cover the full product requirement (see the gap register). |
| **Blocked** | Endpoint exists but its effect depends on an unresolved external dependency. |

Every `/api/v1` operation can additionally return `429 too_many_requests` (rate limit), `503 rate_limiter_unavailable` (shared limiter down), `413 payload_too_large` (body over `MAX_REQUEST_BYTES`, default 30 MB), and `500 internal_error`. These are not repeated per row.

Handler paths are relative to `backend/app/api/`.

## 1. Operations (public)

| Method | Path | Purpose / screen | Auth | Success | Errors | Status | Handler |
|---|---|---|---|---|---|---|---|
| GET | `/health` | Liveness probe | None | 200 `{"status":"ok"}` | — | Implemented — tested | `health.py:health` |
| GET | `/ready` | Readiness: DB, schema revision, object storage, Redis, workers | None | 200 / 503 `{"status","checks","informational"}` | 503 when any required check fails | Implemented — tested | `health.py:ready` |
| GET | `/metrics` *(not in OpenAPI)* | Prometheus scrape | `Bearer <METRICS_TOKEN>` when set | 200 text | 401 | Implemented — tested | `health.py:metrics` |

Not for frontend use except an optional "service status" indicator using `/health`.

## 2. Authentication (`/api/v1/auth`)

| Method | Path | Purpose / screen | Auth | Roles | Success | Main error codes | Status | Handler |
|---|---|---|---|---|---|---|---|---|
| POST | `/api/v1/auth/register` | Citizen self-registration (always PUBLIC) | None | — | 201 `TokenResponse` | 409 `duplicate`; 422 `weak_password`, `invalid_phone`; 429 | **Newly added — tested** | `v1/auth.py:register` |
| POST | `/api/v1/auth/forgot-password` | Request PUBLIC password-reset token | None | — | 200 `MessageResponse` (anti-enumeration) | 422; 429 | **Newly added — tested** (SMS delivery blocked without provider) | `v1/auth.py:forgot_password` |
| POST | `/api/v1/auth/reset-password` | Complete PUBLIC password reset | None | — | 204 | 401 `invalid_reset_token`; 422 `weak_password`; 429 | **Newly added — tested** | `v1/auth.py:reset_password` |
| POST | `/api/v1/auth/login` | Login screen (staff + PUBLIC) | None | — | 200 `TokenResponse` | 401 `invalid_credentials`; 429 `account_temporarily_locked`, `too_many_requests`; 422 | Implemented — tested | `v1/auth.py:login` |
| POST | `/api/v1/auth/refresh` | Silent token refresh | None (refresh token in body) | — | 200 `TokenResponse` | 401 `invalid_refresh_token`, `session_revoked`; 422 | Implemented — tested | `v1/auth.py:refresh` |
| POST | `/api/v1/auth/logout` | Logout | Bearer | All roles | 204 | 401 | Implemented — tested | `v1/auth.py:logout` |
| GET | `/api/v1/auth/me` | Current user, role, permissions (app shell) | Bearer | All roles | 200 `MeResponse` | 401 | Implemented — tested | `v1/auth.py:me` |
| POST | `/api/v1/auth/change-password` | Change own password | Bearer | All roles | 204 (all sessions revoked) | 422 `invalid_current_password`, `weak_password`; 429 | Implemented — tested | `v1/auth.py:change_password` |
| POST | `/api/v1/auth/mfa/enroll` | Start TOTP enrollment | Bearer | All roles | 200 `MfaEnrollResponse` | 409 `mfa_disabled`, `mfa_already_enabled` | Implemented — tested (disabled by default) | `v1/auth.py:enroll_mfa` |
| POST | `/api/v1/auth/mfa/confirm` | Confirm TOTP enrollment | Bearer | All roles | 204 | 409 `mfa_disabled`, `mfa_already_enabled`, `mfa_enrollment_missing`; 422 `mfa_code_invalid` | Implemented — tested (disabled by default) | `v1/auth.py:confirm_mfa` |

## 3. Stations (`/api/v1/stations`)

| Method | Path | Purpose / screen | Roles | Success | Main error codes | Status | Handler |
|---|---|---|---|---|---|---|---|
| GET | `/api/v1/stations` | Station/help-desk dropdowns; resolve `station_id` to a name | All roles | 200 `StationRead[]` (not paginated) | 401 | Implemented — tested | `v1/admin.py:list_stations` |
| POST | `/api/v1/stations` | Admin: create station | Admin | 201 `StationRead` | 403 `permission_denied`; 409 `duplicate`; 422 | Implemented — tested | `v1/admin.py:create_station` |
| PATCH | `/api/v1/stations/{station_id}` | Admin: rename / deactivate | Admin | 200 `StationRead` | 403; 404 `station_not_found`; 409 `duplicate` | Implemented — tested | `v1/admin.py:update_station` |

Query: `GET /stations?include_inactive=true` (any role).

## 4. Users (`/api/v1/users`)

| Method | Path | Purpose / screen | Roles | Success | Main error codes | Status | Handler |
|---|---|---|---|---|---|---|---|
| GET | `/api/v1/users/assignable-officers` | Assign-case dialog | Police, Admin | 200 `OfficerSummary[]` | 403 `permission_denied` (help desk), `station_scope` | Implemented — tested | `v1/admin.py:assignable_officers` |
| GET | `/api/v1/users/directory` | Resolve user ids to display names (case screens) | All roles | 200 `UserDirectoryEntry[]` | 401; 422 when `ids` empty or >100 | Implemented — tested | `v1/admin.py:user_directory` |
| GET | `/api/v1/users` | User management list | Admin | 200 `Page[UserRead]` | 403 | Implemented — tested | `v1/admin.py:list_users` |
| POST | `/api/v1/users` | Create user | Admin | 201 `UserRead` | 409 `duplicate`; 422 `station_required`, `invalid_station`, `badge_required`, `weak_password` | Implemented — tested | `v1/admin.py:create_user` |
| GET | `/api/v1/users/{user_id}` | User detail | Admin | 200 `UserRead` | 404 `user_not_found` | **Implemented — no test found** | `v1/admin.py:get_user` |
| PATCH | `/api/v1/users/{user_id}` | Edit, change role/station, deactivate | Admin | 200 `UserRead` | 403 `self_lockout`; 404; 409 `duplicate`; 422 | Implemented — tested | `v1/admin.py:update_user` |
| POST | `/api/v1/users/{user_id}/reset-password` | Admin password reset | Admin | 204 (user's sessions revoked) | 403 `mfa_enrollment_required`, `step_up_required` (only when `MFA_MODE=totp`); 404; 422 `weak_password`; 429 | Implemented — tested | `v1/admin.py:reset_password` |

There is no delete-user endpoint; deactivate with `PATCH {"is_active": false}`.

## 5. Locations (`/api/v1/locations`)

| Method | Path | Purpose / screen | Roles | Success | Main error codes | Status | Handler |
|---|---|---|---|---|---|---|---|
| GET | `/api/v1/locations` | Location picker (all kinds) | All roles | 200 `LocationRead[]` (max 1000, not paginated) | 401 | Implemented — tested | `v1/locations.py:list_locations` |
| GET | `/api/v1/locations/sectors` | Sector picker | All roles | 200 `LocationRead[]` | 401 | Implemented — tested | `v1/locations.py:list_sectors` |
| GET | `/api/v1/locations/ghats` | Ghat picker, optionally by sector | All roles | 200 `LocationRead[]` | 401 | Implemented — tested | `v1/locations.py:list_ghats` |
| GET | `/api/v1/locations/{location_id}` | Resolve a `location_id` | All roles | 200 `LocationRead` | 404 `location_not_found` | Implemented — tested (QA harness) | `v1/locations.py:get_location` |
| POST | `/api/v1/locations` | Admin: add place | Admin | 201 `LocationRead` | 409 `location_code_taken`; 422 `invalid_sector`, `invalid_station` | Implemented — tested | `v1/locations.py:create_location` |
| PATCH | `/api/v1/locations/{location_id}` | Admin: edit / deactivate place | Admin | 200 `LocationRead` | 404; 409; 422 | Implemented — tested | `v1/locations.py:update_location` |

## 6. Case-type endpoints (`/api/v1/cases/{type}`)

The same seven operations are generated for each case type from `backend/app/services/case_types.py`, with type-specific bodies. `{type}` is one of the slugs below. Handlers are built by `v1/cases.py:build_case_type_router`; route names are `{operation}_{relationship}`.

| `{type}` slug | `case_type` | Relationship | Case number code | OpenAPI tag |
|---|---|---|---|---|
| `missing-persons` | `MISSING_PERSON` | `missing_person` | `MP` | Missing person cases |
| `found-persons` | `FOUND_PERSON` | `found_person` | `FP` | Found person cases |
| `lost-items` | `LOST_ITEM` | `lost_item` | `LI` | Lost item cases |
| `found-items` | `FOUND_ITEM` | `found_item` | `FI` | Found item cases |

| Method | Path | Purpose / screen | Roles | Success | Main error codes | Side effects | Status |
|---|---|---|---|---|---|---|---|
| POST | `/api/v1/cases/{type}` | Registration form | All roles | 201 `{Type}CaseRead` (header `Idempotent-Replayed: true` on replay) | 403 `station_scope`; 409 `idempotency_key_reused`; 422 `station_required`, `invalid_station`, `invalid_location`, `coordinates_out_of_area`, `invalid_idempotency_key` | Allocates case number; status `OPEN`; status-history row; audit `CASE_CREATED`; encrypted contact; **automatic match suggestions** (best effort) | Implemented — tested |
| GET | `/api/v1/cases/{type}` | Type-specific case list | All roles | 200 `Page[{Type}CaseRead]` (full records) | 401; 422 | — | Implemented — tested |
| GET | `/api/v1/cases/{type}/{case_id}` | Case details | All roles (scoped) | 200 `{Type}CaseRead` | 404 `case_not_found` (also for a case of another type) | — | Implemented — tested |
| PATCH | `/api/v1/cases/{type}/{case_id}` | Edit case | All roles (scoped) | 200 `{Type}CaseRead` | 404; 409 `case_changed`, `case_closed`; 422 `required_field`, `coordinate_source_required` | Audit `CASE_UPDATED` / `CONTACT_UPDATED`; re-runs matching when details or position change | Implemented — tested |
| POST | `/api/v1/cases/{type}/{case_id}/assign` | Assign / reassign dialog | Police, Admin | 200 `{Type}CaseRead` | 403 `permission_denied`, `station_scope`; 404; 409 `case_changed`, `case_inactive`, `already_assigned`; 422 `invalid_assignee` | Closes previous assignment row; audit `CASE_ASSIGNED`; auto-transition to `ASSIGNED` from `OPEN`/`UNDER_REVIEW`/`REOPENED` | Implemented — tested |
| POST | `/api/v1/cases/{type}/{case_id}/status` | Status actions (review, close, resolve, reopen) | Depends on target (see section 12) | 200 `{Type}CaseRead` | 403; 404; 409 `invalid_status_transition`, `case_changed`, `assignee_required`, `custody_not_released`, `case_anonymized`; 422 `match_required`, `invalid_match`, `match_not_confirmed`, `verification_required`, `safeguarding_evidence_required` | Status-history row; audit `CASE_STATUS_CHANGED` / `CASE_CLOSED` / `CASE_REOPENED` | Implemented — tested |
| POST | `/api/v1/cases/found-items/{case_id}/custody` | **Found items only.** Custody panel | Police, Admin | 200 `FoundItemCaseRead` | 403; 404; 409 `case_closed`, `invalid_custody_transition`; 422 `storage_location_required`, `handover_required` | Updates `custody_status`, `storage_location`, `handover_acknowledgement`, `released_at`; audit `CUSTODY_CHANGED` | Implemented — tested |

That is 4 × 6 + 1 = **25 operations**.

## 7. Shared case operations (`/api/v1/cases`)

These work for any case type.

| Method | Path | Purpose / screen | Roles | Success | Main error codes | Side effects | Status | Handler (`v1/cases.py`) |
|---|---|---|---|---|---|---|---|---|
| GET | `/api/v1/cases` | Global search across types | All roles; `all_stations=true` Police/Admin | 200 `Page[CaseSummary]` | 403 (`all_stations` for help desk) | — | Implemented — tested | `search_cases` |
| GET | `/api/v1/cases/nearby` | "Cases near me" | All roles; `all_stations` Police/Admin | 200 `NearbyCase[]` | 422 `radius_too_large` | — | Implemented — tested | `nearby_cases` |
| GET | `/api/v1/cases/map` | Map view markers | All roles; `all_stations` Police/Admin | 200 `CaseMap` | 422 `invalid_bbox`, `bbox_too_large` | — | Implemented — tested | `map_cases` |
| GET | `/api/v1/cases/{case_id}` | Open any case by id (type-agnostic) | All roles (scoped) | 200 one of the four `…CaseRead` | 404 | — | Implemented — tested | `get_any_case` |
| POST | `/api/v1/cases/{case_id}/contact/reveal` | "Reveal contact" button | Police, Admin | 200 `ContactRead` | 403; 404 `case_not_found`, `contact_not_found`; 422 | Audit `CONTACT_VIEWED` with `reason_code` | Implemented — tested | `reveal_contact` |
| GET | `/api/v1/cases/{case_id}/notes` | Notes tab | All roles (sensitive notes only for Police/Admin) | 200 `Page[NoteRead]` (oldest first) | 404 | — | Implemented — tested | `list_notes` |
| POST | `/api/v1/cases/{case_id}/notes` | Add note | All roles; `is_sensitive=true` Police/Admin | 201 `NoteRead` | 403; 404 `note_not_found`; 422 `blank_note`, `correction_target_required`, `unexpected_correction_target` | Audit `NOTE_ADDED` | Implemented — tested | `add_note` |
| GET | `/api/v1/cases/{case_id}/attachments` | Photos/recordings tab | All roles | 200 `Page[AttachmentRead]` | 404 | — | Implemented — tested | `list_attachments` |
| POST | `/api/v1/cases/{case_id}/attachments` | Upload photo/voice (multipart) | All roles | 201 `AttachmentRead` (scanned) or **202** (scan pending) | 409 `case_closed`; 413; 415 `unsupported_media_type`, `media_type_mismatch`, `content_type_mismatch`; 422 `empty_file`, `malware_detected`; 503 `storage_unavailable` | Object stored in private bucket; audit `ATTACHMENT_UPLOADED`; malware scan | Implemented — tested | `upload_attachment` |
| GET | `/api/v1/cases/{case_id}/attachments/{attachment_id}` | View/download (short-lived URL) | All roles | 200 `AttachmentDownload` | 404 `attachment_not_found`; 409 `attachment_not_available`; 503 | Audit `ATTACHMENT_ACCESSED` | Implemented — tested | `download_attachment` |
| DELETE | `/api/v1/cases/{case_id}/attachments/{attachment_id}` | Withdraw attachment (**JSON body required**) | Police, Admin | 204 | 403; 404; 422 | Soft delete; audit `ATTACHMENT_DELETED` | Implemented — tested | `delete_attachment` |
| GET | `/api/v1/cases/{case_id}/history` | History tab (status + assignments) | All roles | 200 `CaseHistory` | 404 | — | Implemented — tested | `history` |
| GET | `/api/v1/cases/{case_id}/matches` | Matching candidates for a case | All roles | 200 `MatchRead[]` (not paginated; highest score first) | 404 | — | Implemented — tested | `list_matches` |
| POST | `/api/v1/cases/{case_id}/matches` | Propose a manual match | All roles | 201 `MatchRead` | 404 `case_not_found`; 409 `case_closed`, `duplicate_match`; 422 `incompatible_case_types` | Audit `MATCH_PROPOSED` (both sides); grants cross-station visibility | Implemented — tested | `propose_match` |
| POST | `/api/v1/cases/{case_id}/matches/generate` | "Find matches now" | All roles | 200 `MatchGenerateResult` | 409 `case_not_matchable` | New `AUTOMATIC` `PROPOSED` matches; audit `MATCH_SUGGESTED` | Implemented — tested | `generate_matches` |
| POST | `/api/v1/cases/{case_id}/matches/{match_id}/review` | Confirm / reject match | Police, Admin (not the proposer of a manual match) | 200 `MatchRead` | 403 `separation_of_duties`; 404 `match_not_found`; 409 `match_already_reviewed`, `case_changed`, `case_not_confirmable`, `conflicting_confirmed_match` | On confirm: both cases → `MATCH_FOUND`; audit `MATCH_REVIEWED` | Implemented — tested | `review_match` |
| POST | `/api/v1/cases/{case_id}/matches/{match_id}/revoke` | Undo a wrong confirmed match | Police, Admin | 200 `MatchRead` | 404; 409 `match_not_confirmed`, `case_inactive` | Cases leave `MATCH_FOUND`; audit `MATCH_REVOKED` | Implemented — tested | `revoke_match` |
| POST | `/api/v1/cases/{case_id}/notifications` | Queue SMS/WhatsApp to family/owner | All roles | **202** `NotificationRead` | 409 `case_closed`; 422 `invalid_phone`, `unsupported_locale` | Outbox row; audit `NOTIFICATION_QUEUED` | **Blocked** for delivery (no provider; stays `PENDING`) | `queue_notification` |
| GET | `/api/v1/cases/{case_id}/notifications` | Messages sent for a case | All roles | 200 `NotificationRead[]` | 404 | — | Implemented — tested | `list_notifications` |

That is **19 operations**.

## 8. Match review queue (`/api/v1/matches`)

| Method | Path | Purpose / screen | Roles | Success | Errors | Status | Handler |
|---|---|---|---|---|---|---|---|
| GET | `/api/v1/matches` | Cross-case match review queue | All roles | 200 `Page[MatchQueueItem]` | 401 | Implemented — tested | `v1/matches.py:list_match_queue` |
| GET | `/api/v1/matches/{match_id}` | Side-by-side match review detail | All roles with a case in scope | 200 `MatchDetail` | 404 `match_not_found` | Implemented — tested | `get_match` |
| GET | `/api/v1/matches/{match_id}/photos/{side}` | Presigned URL for candidate photo (`left`/`right`) | Police, Admin (`match:review`) | 200 `MatchPhotoUrl` | 403; 404 | Implemented — tested | `get_match_photo` |
| GET | `/api/v1/matches/{match_id}/history` | Match audit history | Police, Admin (`match:review`) | 200 `MatchHistoryEvent[]` | 404 | Implemented — tested | `get_match_history` |

Query on list: `status` (default `PROPOSED`), `confidence_band`, `match_domain`, `source` (`MANUAL`/`AUTOMATIC`/`FACE`), `limit`, `offset`. Scoped like dashboard `pending_match_reviews`. Confirm/reject still uses `POST /cases/{id}/matches/{mid}/review`. FACE candidates require two different officers before confirmation. Embeddings are never returned.

## 8b. Staff notifications (`/api/v1/staff-notifications`)

| Method | Path | Purpose / screen | Roles | Success | Errors | Status | Handler |
|---|---|---|---|---|---|---|---|
| GET | `/api/v1/staff-notifications` | In-app notifications for the caller | All roles | 200 `Page[StaffNotificationRead]` | 401 | Implemented — tested | `list_notifications` |
| POST | `/api/v1/staff-notifications/{id}/read` | Mark notification read | Recipient only | 200 `StaffNotificationRead` | 404 | Implemented — tested | `mark_read` |

Query: `status` (`UNREAD`/`READ`), `limit`, `offset`. Each item includes `match_id` and `link` (`/matches/{id}`). No SMS/WhatsApp/email in this phase.

## 9. Audit (`/api/v1/audit-events`)

| Method | Path | Purpose / screen | Roles | Success | Errors | Status | Handler |
|---|---|---|---|---|---|---|---|
| GET | `/api/v1/audit-events` | Audit log viewer | Admin | 200 `Page[AuditEventRead]` (newest first) | 403 | Implemented — tested | `v1/audit.py:list_audit_events` |

## 10. Retention and legal holds (Admin only)

The whole router depends on `retention:manage`, so non-admins get `403` before validation.

| Method | Path | Purpose | Success | Main error codes | Status | Handler (`v1/retention.py`) |
|---|---|---|---|---|---|---|
| GET | `/api/v1/retention/policies` | Policy catalogue | 200 `RetentionPolicyRead[]` | 403 | Implemented — tested | `list_policies` |
| POST | `/api/v1/retention/runs` | Plan (dry run) | 201 `RetentionRunRead` | 422 | Implemented — tested | `plan_run` |
| GET | `/api/v1/retention/runs` | List runs (`limit` ≤100, default 25; `offset`) | 200 `RetentionRunRead[]` (plain list) | — | Implemented — tested | `list_runs` |
| GET | `/api/v1/retention/runs/{run_id}` | Run detail | 200 | 404 `retention_run_not_found` | Implemented — tested | `get_run` |
| POST | `/api/v1/retention/runs/{run_id}/approve` | Second-admin approval | 200 | 409 `two_person_rule`, `retention_run_state` | Implemented — tested | `approve_run` |
| POST | `/api/v1/retention/runs/{run_id}/cancel` | Cancel | 200 | 409 `retention_run_state` | Implemented — tested | `cancel_run` |
| POST | `/api/v1/retention/runs/{run_id}/execute` | Execute approved run | 200 | 409 `retention_run_state`, `execute_via_retention_task` (anonymisation in staging/production); 403 step-up | Implemented — tested | `execute_run` |
| GET | `/api/v1/cases/{case_id}/legal-holds` | Holds on a case | 200 `LegalHoldRead[]` | 404 | Implemented — tested | `list_holds` |
| POST | `/api/v1/cases/{case_id}/legal-holds` | Place hold | 201 `LegalHoldRead` | 404 | Implemented — tested | `place_hold` |
| POST | `/api/v1/cases/{case_id}/legal-holds/{hold_id}/release` | Release hold | 200 `LegalHoldRead` | 404 `legal_hold_not_found`; 409 `legal_hold_released` | Implemented — tested | `release_hold` |

## 11. Dashboard and reports

| Method | Path | Purpose / screen | Roles | Success | Errors | Status | Handler |
|---|---|---|---|---|---|---|---|
| GET | `/api/v1/dashboard/summary` | Home dashboard counts (scoped); optional `from`/`to` Asia/Kolkata dates | Staff (`case:read`) | 200 `DashboardSummary` | 401; 422 `invalid_range` | **Modified — tested** (date filtering added; no location/activity feed) | `v1/dashboard.py:summary` |
| GET | `/api/v1/reports/cases.csv` | CSV export | All roles (scoped) | 200 `text/csv`, headers `X-Export-Rows`, `X-Export-Truncated` | 422 `invalid_range`, `range_too_large` | **Partial** (one report, ≤31 days, ≤1000 rows, 6 columns) | `v1/reports.py:export_cases` |

## 11b. Public citizen portal (`/api/v1/public`)

Unauthenticated except where noted. OTP and submit routes are IP rate-limited (`RATE_LIMIT_PUBLIC_*`).

| Method | Path | Purpose / screen | Auth | Success | Main error codes | Status | Handler |
|---|---|---|---|---|---|---|---|
| POST | `/api/v1/public/otp/request` | Pre-verify mobile for report/sighting | None | 200 `OtpRequestResponse` | 422 `wrong_endpoint`; 429 | Implemented — tested | `v1/public.py:request_otp` |
| POST | `/api/v1/public/otp/verify` | Issue verified-mobile token | None | 200 `VerifiedMobileResponse` | 401; 422; 429 | Implemented — tested | `v1/public.py:verify_otp` |
| POST | `/api/v1/public/status/request-otp` | Status OTP (always non-enumerating) | None | 200 `OtpRequestResponse` | 422; 429 | Implemented — tested | `v1/public.py:status_request_otp` |
| POST | `/api/v1/public/status/verify-otp` | Case-scoped status JWT | None | 200 `CaseStatusTokenResponse` | 401; 422; 429 | Implemented — tested | `v1/public.py:status_verify_otp` |
| GET | `/api/v1/public/cases/{case_id}/status` | Privacy-safe public status | Bearer status JWT | 200 `PublicCaseStatusRead` | 401; 404 | Implemented — tested | `v1/public.py:case_status` |
| POST | `/api/v1/public/missing-persons` | Citizen missing-person report | `X-Verified-Mobile-Token` + `Idempotency-Key` | 201/200 `PublicSubmissionAck` | 401; 409; 413; 415; 422; 429; 503 | Implemented — tested | `v1/public.py:submit_missing_person` |
| POST | `/api/v1/public/sightings` | Citizen sighting (+ optional photo) | `X-Verified-Mobile-Token` | 201 `PublicSightingAck` | 401; 413; 415; 422; 429; 503 | Implemented — tested | `v1/public.py:submit_sighting` |
| GET | `/api/v1/public/information` | Published bilingual public info | None | 200 `PublicInformationList` | — | Implemented — tested | `v1/public.py:public_information` |

Staff extensions:

| Method | Path | Purpose | Roles | Success | Status | Handler |
|---|---|---|---|---|---|---|
| POST | `/api/v1/cases/{case_id}/public-review` | Verify/reject public report | `case:review` | 200 case summary | Implemented — tested | `v1/cases.py:public_review` |
| GET | `/api/v1/sightings` | Station-scoped sighting list | `sighting:read` | 200 `Page[SightingRead]` | Implemented — tested | `v1/public.py:list_sightings` |
| GET | `/api/v1/sightings/{sighting_id}` | Sighting detail | `sighting:read` | 200 `SightingRead` | Implemented — tested | `v1/public.py:get_sighting` |
| GET | `/api/v1/sightings/{sighting_id}/photo` | Presigned sighting photo | `sighting:read` | 200 `AttachmentDownload` | Implemented — tested | `v1/public.py:get_sighting_photo` |
| GET/POST/PATCH | `/api/v1/admin/public-information…` | Admin list/create/update/publish/unpublish | `public_information:manage` | 200/201 | Implemented — tested | `v1/public.py:admin_*` |
| POST/DELETE | `/api/v1/devices/push-token` | Register/deactivate FCM token | Staff JWT or citizen status JWT | 204 | Implemented — tested | `v1/devices.py` |

## 12. Totals

| Module | Operations |
|---|---|
| Operations (`/health`, `/ready`) | 2 |
| Authentication | 7 |
| Stations | 3 |
| Users | 7 |
| Locations | 6 |
| Case-type endpoints | 25 |
| Shared case operations | 20 (+ public-review) |
| Match review queue | 4 |
| Staff notifications | 2 |
| Audit | 1 |
| Retention and legal holds | 10 |
| Dashboard | 1 |
| Reports | 1 |
| Public portal | 8 |
| Sightings (staff) | 3 |
| Public information admin | 5 |
| Devices | 2 |
| **Total in OpenAPI** | **91 paths / 113 operations** |
| `/metrics` (outside OpenAPI) | 1 |

Phase 4 adds citizen OTP intake, status access, sightings, lifecycle EMAIL/PUSH outbox adapters (MSG91/SMTP/FCM), and public information. PUBLIC self-registration / restricted case access and password-reset token APIs are also documented above. Live SMS/email/push delivery remains **blocked / not verified** until credentials and DLT/APNs approvals are supplied. See [PHASE4_PUBLIC_PORTAL.md](../evidence/PHASE4_PUBLIC_PORTAL.md).

## 13. Role → permission matrix (from `backend/app/auth/permissions.py`)

`GET /auth/me` returns the caller's permission strings in `permissions`. Use them for UI gating instead of hard-coding roles. **PUBLIC always receives `permissions: []`.**

| Permission string | Help desk | Police | Admin | PUBLIC | Used by |
|---|---|---|---|---|---|
| `case:create` | ✓ | ✓ | ✓ | | Register cases (PUBLIC create uses route allowlist, not this string) |
| `case:read` | ✓ | ✓ | ✓ | | All case reads, notes, attachments list, history, matches, match queue, user directory, notifications list, dashboard, CSV |
| `case:update` | ✓ | ✓ | ✓ | | PATCH case |
| `case:review` | ✓ | ✓ | ✓ | | Status → `UNDER_REVIEW`; public-report verify/reject |
| `sighting:read` | ✓ | ✓ | ✓ | | Staff sighting list/detail/photo |
| `note:create` | ✓ | ✓ | ✓ | | Add note |
| `attachment:upload` | ✓ | ✓ | ✓ | | Upload |
| `attachment:download` | ✓ | ✓ | ✓ | | Download URL |
| `match:propose` | ✓ | ✓ | ✓ | | Propose match, generate matches |
| `notification:send` | ✓ | ✓ | ✓ | | Queue notification |
| `user:list_assignable` | | ✓ | ✓ | | Assignable officers |
| `case:search_all_stations` | | ✓ | ✓ | | `all_stations=true` |
| `case:assign` | | ✓ | ✓ | | Assign; status → `ASSIGNED` |
| `case:resolve` | | ✓ | ✓ | | Status → `MATCH_FOUND`, `RESOLVED`, `CLOSED`, `REOPENED` |
| `case:custody_update` | | ✓ | ✓ | | Custody |
| `case:contact_read` | | ✓ | ✓ | | Contact reveal |
| `note:sensitive` | | ✓ | ✓ | | Create/read sensitive notes; see `verification_evidence`, `review_evidence`, `revocation_reason` |
| `attachment:delete` | | ✓ | ✓ | | Delete attachment |
| `match:review` | | ✓ | ✓ | | Confirm/reject/revoke; match photo URLs |
| `user:manage` | | | ✓ | | Users CRUD, reset password |
| `station:manage` | | | ✓ | | Stations create/update |
| `location:manage` | | | ✓ | | Locations create/update |
| `retention:manage` | | | ✓ | | Retention, legal holds |
| `audit:read` | | | ✓ | | Audit log |
| `public_information:manage` | | | ✓ | | Public information publish/unpublish |

### Case visibility (row-level scope, `backend/app/auth/scope.py`)

| Role | Sees |
|---|---|
| Admin | Every case. |
| Police | Cases at own station, cases assigned to them, cases they reported, and cases linked to an own-station case by a `CONFIRMED` match or a `MANUAL` `PROPOSED` match. Automatic suggestions alone never widen access. |
| Help desk | Cases at own station that are **not archived**. |
| PUBLIC | Only non-archived cases where `reported_by_user_id` is the authenticated citizen. |

With `all_stations=true`, police and admins also receive **summaries** of out-of-scope cases (`in_scope: false`); opening such a case by id still returns `404` unless it is in scope.
