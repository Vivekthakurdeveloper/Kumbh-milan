# API gap analysis

This register lists what the frontend needs that the backend does not provide, plus two defects found by code inspection. Everything under "Suggested implementation" is a **recommendation**. None of it exists in the code, and no application code was changed during this exercise.

Gap IDs are stable and are cited from the other documents in this folder.

**Priority:**

| Priority | Meaning |
|---|---|
| **P0** | Defect or blocker that breaks an expected flow. Fix before frontend release. |
| **P1** | Needed for an operationally complete product; the frontend can ship a workaround only temporarily. |
| **P2** | Important usability or efficiency improvement. |
| **P3** | Nice to have / future. |

**Mandatory vs optional:** "Mandatory" gaps block a core lost-and-found or reunification flow, or cause errors. "Optional" gaps improve the product but the core flows work without them.

## 1. Summary

| ID | Feature | Priority | Class | Blocks screen | Status |
|---|---|---|---|---|---|
| G-15 | `language` accepts 200 chars, database stores 100 → server error | **P0** | Mandatory (defect) | Registration, edit (persons) | **Resolved** (schema max 100) |
| G-16 | CSV export headers not readable cross-origin | P0 if cross-origin, else none | Mandatory (deployment) | Reports | **Resolved** |
| G-17 | `X-Step-Up-Code` blocked by CORS | P0 if `MFA_MODE=totp` and cross-origin | Mandatory (deployment) | User management, retention | **Resolved** |
| G-1 | Match review queue across cases | P1 | Mandatory | Matching queue, dashboard | **Resolved** (`GET /matches`) |
| G-2 | Claimant / handover record and reunification record | P1 | Mandatory | Reunification, custody | **Resolved in worktree; rollout and DB regression pending** |
| G-3 | Resolve user ids to display names for non-admins | P1 | Mandatory | Case details, lists | **Resolved** (`GET /users/directory`) |
| G-7 | Custody history visible to case handlers | P1 | Mandatory | Custody, case details | **Resolved** (`CaseHistory.custody_history`) |
| G-9 | Real SMS/WhatsApp delivery to families | P1 (P0 if family messaging is launch scope, Q-4) | Mandatory if in scope | Family notifications | Open |
| G-14 | `CaseSummary.location_id` always `null` | P1 | Mandatory (defect) | Search, nearby, matching | **Resolved** (`to_summary` sets it; age/gender also added) |
| G-4 | Unassign, team assignment, assign to help desk | P2 | Optional | Assign | Open |
| G-5 | Reference data: item categories and enums | P2 | Optional | Registration, filters | Open |
| G-8 | Dashboard breakdowns, trends, activity feed | P2 | Optional | Dashboard | Partial — `from`/`to` Asia/Kolkata date filtering added; location/activity feed still open |
| G-10 | More list filters (location_id, sector, custody, reporter) | P2 | Optional | Case lists | Open |
| G-11 | More sort orders (urgency, event time) | P2 | Optional | Case lists | Open |
| G-12 | Duplicate-registration detection | P2 | Optional | Registration | Open |
| G-19 | Staff in-app notifications | P2 | Optional | Staff notifications | Partial — list/read feed exists; unread count and broader event coverage remain |
| G-21 | Additional reports and aggregates | P2 | Optional | Reports | Open |
| G-6 | Supervisor approval and assignee notification | P3 | Optional | Assign | Open |
| G-13 | Self-service password reset | P3 | Optional | Login | **Resolved** (`POST /auth/forgot-password`, `POST /auth/reset-password`; SMTP email; SMS adapter requires approved provider/DLT configuration) |
| G-18 | Discoverable MFA mode | P3 | Optional | Profile | Open |
| G-20 | Realtime updates (SSE/WebSocket) | P3 | Optional | Dashboard, lists | Open |
| G-22 | Per-case activity timeline for non-admins | P3 | Optional | Case details | Partial — status, assignments, and field changes are available; custody/notes/media remain separate |
| G-23 | Photo-based or similarity matching | P3 | Optional | Matching | **Resolved in code; production approval/calibration blocked** |
| G-24 | Runtime system settings API | P3 | Optional | System settings | Open |

### Resolved in Phase 1 portal backend work

- **G-15:** `language` on missing/found person create/update is now `max_length=100`, matching the database columns. Values of 101–200 characters return `422 validation_failed`.
- **G-14:** `to_summary` sets `location_id=case.location_id`. `CaseSummary` also includes optional `approximate_age`, `age_group`, and `gender` (from `estimated_gender` for found persons). Redacted counterparts null these fields.
- **G-1:** `GET /api/v1/matches` returns a paginated review queue (`MatchQueueItem` with `left_case` / `right_case`), scoped like the dashboard `pending_match_reviews` count.
- **G-3:** `GET /api/v1/users/directory?ids=…` returns `id`, `full_name`, `role`, `station_id` for any role with `case:read`.

## 2. Defects (found by code inspection, not executed)

### G-15 — `language` length mismatch — **RESOLVED**

| Field | Detail |
|---|---|
| Feature | Person case registration and edit |
| Resolution | `language` on missing/found person schemas is now `LanguageText` (`max_length=100`), matching the `VARCHAR(100)` columns. Covered by `test_language_over_100_chars_returns_422`. |
| Priority | Was **P0** |

### G-14 — `CaseSummary.location_id` never populated — **RESOLVED**

| Field | Detail |
|---|---|
| Feature | Search results, nearby results, match counterparts |
| Resolution | `to_summary` sets `location_id=case.location_id` and also populates `approximate_age`, `age_group`, and `gender`. Redacted counterparts null these fields. Covered by `test_case_summary_includes_location_id_age_and_gender`. |
| Priority | Was P1 |

## 3. Deployment-dependent gaps

### G-16 — Export headers not exposed via CORS — **RESOLVED**

| Field | Detail |
|---|---|
| Resolution | `Content-Disposition`, `X-Export-Rows`, and `X-Export-Truncated` are exposed by CORS in `backend/app/main.py`. |
| Verification | `test_cors_exposes_export_metadata_to_approved_origin` verifies the browser-visible response contract. |
| Priority | Was P0 when the frontend is on a different origin. |

### G-17 — Step-up header not allowed by CORS — **RESOLVED**

| Field | Detail |
|---|---|
| Resolution | `X-Step-Up-Code` is allowed by CORS in `backend/app/main.py`; the server still validates the TOTP code and session when step-up is enabled. |
| Verification | `test_cors_allows_step_up_header_for_approved_origin` covers the positive preflight, and the CORS contract also rejects unapproved origins. |
| Priority | Was P0 when `MFA_MODE=totp` and the frontend is cross-origin. |

## 4. Mandatory functional gaps

### G-1 — Match review queue — **RESOLVED**

| Field | Detail |
|---|---|
| Resolution | `GET /api/v1/matches` returns `Page[MatchQueueItem]` with `left_case` / `right_case` summaries, scoped like the dashboard count, default `status=PROPOSED`. Covered by Phase 1 portal tests. |
| Priority | Was P1 |

### G-2 — Claimant and reunification record — **RESOLVED IN WORKTREE**

| Field | Detail |
|---|---|
| Required behaviour | When a person is reunified or an item returned, record who collected (name, relationship, ID document type and reference, handover time, staff witness), linked to both cases. |
| Resolution | Revision `0014` adds `case_handovers` with claimant, document type/reference, optional witness, verification/rejection, physical handover, acknowledgement, case/match links, optimistic versioning, and duplicate-completion protection. |
| APIs | Police/admin create, list, read, verify/reject, complete, and read lifecycle history under `/cases/{case_id}/handovers`. |
| Security | Sensitive values are encrypted at rest, omitted from audit payloads, anonymized by retention, and protected by `handover:read` / `handover:manage`. |
| Workflow | `HANDOVER_REQUIRED_FOR_RESOLVE` can require completion before a person case reaches `RESOLVED`; the compatibility default is false until staff clients are deployed. |
| Priority | Was P1 |
| Remaining gate | Migration/full database regression, client rollout, approved production claimant/document policy, then enable the resolution gate. |
| Verification | Lifecycle, rejection, idempotency, authorization, item custody, encrypted storage, history, and resolution-gate tests are added. |

### G-3 — User display names for non-admins — **RESOLVED**

| Field | Detail |
|---|---|
| Resolution | `GET /api/v1/users/directory?ids=…` (1–100 ids) returns `id`, `full_name`, `role`, `station_id` for any role with `case:read`. Covered by `test_user_directory_returns_minimal_fields_for_all_roles`. |
| Priority | Was P1 |

### G-7 — Custody history — **RESOLVED**

| Field | Detail |
|---|---|
| Required behaviour | Chain of custody (who moved the item where, when, released to whom) visible to case handlers. |
| Resolution | `GET /cases/{id}/history` includes chronological `custody_history` entries projected from append-only `CUSTODY_CHANGED` audit events. Each entry records actor, role, time, previous/new status, reason, storage location, and whether handover evidence was recorded. |
| Privacy | PUBLIC callers receive an empty custody history. Handover acknowledgement text is not copied into history. |
| Compatibility | Older audit rows remain readable but may have `storage_location: null`; no historical location is invented. |
| Priority | Was P1 |
| Verification | Workflow coverage records two transfers and a release and verifies police/help-desk visibility without exposing acknowledgement text. |

### G-9 — Family message delivery

| Field | Detail |
|---|---|
| Required behaviour | Queued SMS/WhatsApp messages are delivered and their status updated. |
| Current | The outbox, templates, consent capture, and worker exist. No real provider is integrated; staging/production settings require the provider to be disabled, so messages remain `PENDING`. |
| Missing | Provider integration (e.g. DLT-registered SMS in India), delivery receipts. |
| Impact | Families are not notified; the screen is blocked. |
| Priority | P1 (P0 if family messaging is in launch scope) |
| Dependencies | Q-4 (provider, DLT templates) |
| Acceptance criteria | In staging with a sandbox provider, a queued message reaches `SENT`; failures reach `FAILED` with `last_error`; dashboard `notifications_failed` reflects them. |

## 5. Optional gaps

| ID | Feature | Current | Missing / suggested implementation | Acceptance criteria |
|---|---|---|---|---|
| G-4 | Assignment options | Assign/reassign to a police officer of the case's station. | Unassign endpoint; assign to a team or help-desk user; bulk assign. | Unassigning returns the case to `UNDER_REVIEW` with a history row. |
| G-5 | Reference data | Item categories are free text (2–64). Enum values are only in OpenAPI. | `GET /reference/item-categories` (admin-managed) and optionally `GET /reference/enums` with labels in `en`/`hi`. | Registration dropdowns load from the API; unknown categories rejected if the list is enforced. |
| G-6 | Supervisor approval, assignee notification | Assignment is immediate; assignee is not notified. | Approval workflow; notify via G-19. | Assignee sees the new case in their notification feed. |
| G-8 | Dashboard depth | Scoped counts only. | Breakdowns by station, sector, location, day; median time to resolve; recent activity. | Each figure equals the corresponding filtered `GET /cases` total. |
| G-10 | Filters | Filters listed in [API_REQUEST_RESPONSE_REFERENCE.md §6.4](./API_REQUEST_RESPONSE_REFERENCE.md). | `location_id`, `sector_id`, `custody_status`, `reported_by_user_id`, `has_contact_details`. | Each filter narrows `total` correctly and respects scope. |
| G-11 | Sorting | `created_at`, `updated_at` only. | `-urgency_level`, `event_at`. | Critical cases first with `sort=-urgency_level`. |
| G-12 | Duplicate detection | Only `Idempotency-Key` prevents double submit by the same user. Automatic matching never compares two cases of the same type. | Same-type similarity check on create returning `possible_duplicates`. | Registering the same lost child twice at two desks surfaces the first case. |
| G-13 | Self-service password reset | Resolved: emailed or texted one-time code, see the reference. | — | — |
| G-18 | MFA discovery | Client learns via `409 mfa_disabled`. | `mfa_mode` and `mfa_enabled` in `GET /auth/me`. | — |
| G-19 | Staff notifications | `GET /staff-notifications` and mark-read exist for face-candidate events. | Add unread count and approved assignment/case/grievance event coverage. | — |
| G-20 | Realtime | Polling only. | SSE/WebSocket for case changes and new matches. | — |
| G-21 | Reports | One CSV: 6 columns, ≤31 days, ≤1000 rows, `created_from`/`created_to` only. | More filters, aggregates, PDF, scheduled exports. | — |
| G-22 | Case activity timeline | `/history` gives status, assignment, role-redacted field changes, and custody events; the full audit is admin-only. | Add notes, attachment, and other approved operational events to one case-scoped timeline. | — |
| G-23 | Photo matching | OpenCV YuNet/SFace candidates, encrypted embeddings, match photos, staff notifications, and two-officer confirmation are implemented. | Production use still requires biometric approval, real-event calibration, and key-rotation readiness. | — |
| G-24 | Settings API | Configuration is environment variables. | Admin API for selected runtime settings. | — |

## 6. Dependencies between gaps

- G-6 depends on G-19 for notifying the assignee.
- G-2 should precede report metrics on reunifications (G-21).
- G-16 and G-17 are resolved; same-origin deployment is still recommended to reduce browser-policy complexity (Q-5).

## 7. Open questions

| ID | Question |
|---|---|
| Q-1 | Product name: the request says "Kumbh Milan"; code, OpenAPI title, and case numbers use "Kumbh Setu" (`KSLF-…`). Which should the UI show? |
| Q-2 | Are volunteer or supervisor roles needed? Only `ADMIN`, `POLICE_OFFICER`, `HELP_DESK_OPERATOR` exist. |
| Q-3 | What is the minimum mandatory data for a missing/found person registration? The API requires none. |
| Q-4 | Is SMS/WhatsApp family notification in launch scope, and which provider and DLT templates will be used? |
| Q-5 | Will the frontend be served from the same origin as the API? |
| Q-6 | Will production use `MFA_MODE=totp`? |
| Q-7 | Offline registration for help desks | Closed in Phase 3: `/api/v1/sync/push|pull|status` — see docs/MOBILE_SYNC.md |
| Q-8 | Which UI languages are required? Message templates support `en` and `hi` only. |
| Q-9 | What are the staging and production hostnames? None are defined in the repository. |
| Q-10 | Is there an official item category taxonomy (needed for G-5)? |
| Q-11 | Are the default retention periods approved by the data protection owner? |
