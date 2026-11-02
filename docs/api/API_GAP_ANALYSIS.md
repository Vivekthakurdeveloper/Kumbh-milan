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

| ID | Feature | Priority | Class | Blocks screen |
|---|---|---|---|---|
| G-15 | `language` accepts 200 chars, database stores 100 → server error | **P0** | Mandatory (defect) | Registration, edit (persons) |
| G-16 | CSV export headers not readable cross-origin | P0 if cross-origin, else none | Mandatory (deployment) | Reports |
| G-17 | `X-Step-Up-Code` blocked by CORS | P0 if `MFA_MODE=totp` and cross-origin | Mandatory (deployment) | User management, retention |
| G-1 | Match review queue across cases | P1 | Mandatory | Matching queue, dashboard |
| G-2 | Claimant / handover record and reunification record | P1 | Mandatory | Reunification, custody |
| G-3 | Resolve user ids to display names for non-admins | P1 | Mandatory | Case details, lists |
| G-7 | Custody history visible to case handlers | P1 | Mandatory | Custody, case details |
| G-9 | Real SMS/WhatsApp delivery to families | P1 (P0 if family messaging is launch scope, Q-4) | Mandatory if in scope | Family notifications |
| G-14 | `CaseSummary.location_id` always `null` | P1 | Mandatory (defect) | Search, nearby, matching |
| G-4 | Unassign, team assignment, assign to help desk | P2 | Optional | Assign |
| G-5 | Reference data: item categories and enums | P2 | Optional | Registration, filters |
| G-8 | Dashboard breakdowns, trends, activity feed | P2 | Optional | Dashboard |
| G-10 | More list filters (location_id, sector, custody, reporter) | P2 | Optional | Case lists |
| G-11 | More sort orders (urgency, event time) | P2 | Optional | Case lists |
| G-12 | Duplicate-registration detection | P2 | Optional | Registration |
| G-19 | Staff in-app notifications | P2 | Optional | Staff notifications |
| G-21 | Additional reports and aggregates | P2 | Optional | Reports |
| G-6 | Supervisor approval and assignee notification | P3 | Optional | Assign |
| G-13 | Self-service password reset | P3 | Optional | Login |
| G-18 | Discoverable MFA mode | P3 | Optional | Profile |
| G-20 | Realtime updates (SSE/WebSocket) | P3 | Optional | Dashboard, lists |
| G-22 | Per-case activity timeline for non-admins | P3 | Optional | Case details |
| G-23 | Photo-based or similarity matching | P3 | Optional | Matching |
| G-24 | Runtime system settings API | P3 | Optional | System settings |

## 2. Defects (found by code inspection, not executed)

### G-15 — `language` length mismatch

| Field | Detail |
|---|---|
| Feature | Person case registration and edit |
| Required behaviour | Values that pass request validation are stored, or invalid values return `422`. |
| Current | `MissingPersonCreate/Update` and `FoundPersonCreate/Update` declare `language: ShortText` (max 200, `backend/app/schemas/common.py`). The columns `missing_person_details.language` and `found_person_details.language` are `VARCHAR(100)` (`backend/app/models/case.py`, migration `0001`). A scan of every request schema against every string column found no other mismatch. |
| Missing | Consistent limits. |
| Impact | A 101–200 character value passes validation, then PostgreSQL rejects the insert; the generic handler returns `500 internal_error`. Not reproduced at runtime because the database was unavailable. |
| Suggested impl | Change the schema to `max_length=100` (no migration), or widen the column with a migration. |
| Priority | **P0** |
| Dependencies | None |
| Acceptance criteria | `POST /cases/missing-persons` with a 150-character `language` returns either `201` with the value stored, or `422 validation_failed` with `loc: ["body","language"]`; never `500`. A regression test covers both person types. |

Frontend workaround until fixed: limit the input to 100 characters.

### G-14 — `CaseSummary.location_id` never populated

| Field | Detail |
|---|---|
| Feature | Search results, nearby results, match counterparts |
| Required behaviour | `location_id` reflects the case's reference location. |
| Current | `to_summary` (`backend/app/services/cases.py`) does not set `location_id`, so it is always `null` in `GET /cases`, `GET /cases/nearby`, and every `MatchRead.counterpart`. |
| Missing | Population of the field. |
| Impact | Lists cannot show the place name or group by location without fetching each case. |
| Suggested impl | Set `location_id=case.location_id` in `to_summary`; keep it redacted where counterpart redaction already applies. |
| Priority | P1 |
| Acceptance criteria | A case created with `location_id` returns the same value in `GET /cases` results; redacted counterparts still return `null`. |

## 3. Deployment-dependent gaps

### G-16 — Export headers not exposed via CORS

| Field | Detail |
|---|---|
| Current | `expose_headers` is `X-Request-ID, Idempotent-Replayed, Retry-After` (`backend/app/main.py`). `X-Export-Rows`, `X-Export-Truncated`, and `Content-Disposition` are set by the CSV endpoint but cannot be read cross-origin. |
| Impact | A cross-origin frontend cannot tell users that an export was truncated at 1000 rows. |
| Suggested impl | Add the three headers to `expose_headers`, or deploy same-origin. |
| Priority | P0 if the frontend is on a different origin; none if same-origin |
| Acceptance criteria | From the frontend origin, `response.headers.get("X-Export-Truncated")` returns `"true"` or `"false"`. |

### G-17 — Step-up header not allowed by CORS

| Field | Detail |
|---|---|
| Current | The middleware reads `X-Step-Up-Code` for admin password reset and retention execute when `MFA_MODE=totp`, but `allow_headers` does not include it. |
| Impact | Preflight fails; those two admin actions are impossible cross-origin with TOTP enabled. |
| Suggested impl | Add `X-Step-Up-Code` to `allow_headers`, or deploy same-origin. |
| Priority | P0 if `MFA_MODE=totp` and cross-origin; otherwise none |
| Dependencies | Q-5, Q-6 |
| Acceptance criteria | Cross-origin `POST /users/{id}/reset-password` with `X-Step-Up-Code` passes preflight and returns `204` with a valid code. |

## 4. Mandatory functional gaps

### G-1 — Match review queue

| Field | Detail |
|---|---|
| Required behaviour | Police/admins see all `PROPOSED` matches they can review, sorted by confidence, without opening each case. |
| Current | `GET /cases/{id}/matches` per case; dashboard has a `pending_match_reviews` **count**. |
| Missing | A cross-case list endpoint. |
| Impact | Reviewers would need one request per case (N+1); high-confidence matches of lost children can be missed. |
| Suggested impl | `GET /matches?status=PROPOSED&confidence_band=HIGH&station_id=&limit=&offset=` returning `Page[MatchRead]`, scoped and redacted exactly like the per-case endpoint. |
| Priority | P1 |
| Acceptance criteria | Returns only matches where at least one case is visible to the caller; same redaction as `GET /cases/{id}/matches`; count equals the dashboard value for the same caller. |

### G-2 — Claimant and reunification record

| Field | Detail |
|---|---|
| Required behaviour | When a person is reunified or an item returned, record who collected (name, relationship, ID document type and reference, handover time, staff witness), linked to both cases. |
| Current | Free text: `verification_evidence` on the status change, `recipient_acknowledgement` on custody release, and notes. The resolving match id is stored. |
| Missing | Structured claimant entity, reunification entity, retrieval endpoint. |
| Impact | No structured safeguarding record for handing over children or vulnerable adults; reports cannot count reunifications. |
| Suggested impl | `POST /cases/{id}/handovers` (claimant fields, encrypted like contact details, `match_id`), `GET /cases/{id}/handovers`; require one before `RESOLVED` for person cases. |
| Priority | P1 |
| Dependencies | Q-3 (mandatory fields), data protection review |
| Acceptance criteria | Resolving a person case without a handover record returns `422`; the record is audited and visible only to `case:contact_read` holders. |

### G-3 — User display names for non-admins

| Field | Detail |
|---|---|
| Required behaviour | Show who reported, who is assigned, and who wrote a note. |
| Current | Responses contain user ids only. `GET /users/{id}` is admin-only; `GET /users/assignable-officers` (police/admin) returns officers of one station. Help desk has no way to resolve names. |
| Missing | Minimal user lookup for all roles. |
| Impact | Case details show ids instead of names for help desk; police cannot resolve help-desk reporters. |
| Suggested impl | Either embed `{id, full_name, role}` objects in case/note responses, or add `GET /users/directory?ids=…` returning only `id, full_name, role, station_id` for all roles. |
| Priority | P1 |
| Acceptance criteria | A help-desk user can display the assignee's name; no email, phone, or badge is exposed to non-admins. |

### G-7 — Custody history

| Field | Detail |
|---|---|
| Required behaviour | Chain of custody (who moved the item where, when, released to whom) visible to case handlers. |
| Current | Only the current state is on the case. Each change is an audit event (`CUSTODY_CHANGED`), readable only with `audit:read` (admin). `GET /cases/{id}/history` covers status and assignment only. |
| Missing | A custody history endpoint or inclusion in `CaseHistory`. |
| Suggested impl | Add `custody` entries to `GET /cases/{id}/history`, from a new custody-event table or a filtered view of audit events. |
| Priority | P1 |
| Acceptance criteria | After two transfers and a release, police see three entries with actor, time, status, storage location. |

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
| G-13 | Self-service password reset | Admin reset only. | Out-of-band reset (requires verified email/phone). | — |
| G-18 | MFA discovery | Client learns via `409 mfa_disabled`. | `mfa_mode` and `mfa_enabled` in `GET /auth/me`. | — |
| G-19 | Staff notifications | None. | Notification feed per user, unread count. | — |
| G-20 | Realtime | Polling only. | SSE/WebSocket for case changes and new matches. | — |
| G-21 | Reports | One CSV: 6 columns, ≤31 days, ≤1000 rows, `created_from`/`created_to` only. | More filters, aggregates, PDF, scheduled exports. | — |
| G-22 | Case activity timeline | `/history` gives status and assignment changes; the full audit is admin-only. | Case-scoped, redacted audit timeline for case handlers. | — |
| G-23 | Photo matching | Rule-based matching on attributes, time, location; photos not used. | Optional image similarity (privacy review required). | — |
| G-24 | Settings API | Configuration is environment variables. | Admin API for selected runtime settings. | — |

## 6. Dependencies between gaps

- G-6 depends on G-19 for notifying the assignee.
- G-2 should precede report metrics on reunifications (G-21).
- G-16 and G-17 disappear if the frontend is deployed same-origin (Q-5).

## 7. Open questions

| ID | Question |
|---|---|
| Q-1 | Product name: the request says "Kumbh Milan"; code, OpenAPI title, and case numbers use "Kumbh Setu" (`KSLF-…`). Which should the UI show? |
| Q-2 | Are volunteer or supervisor roles needed? Only `ADMIN`, `POLICE_OFFICER`, `HELP_DESK_OPERATOR` exist. |
| Q-3 | What is the minimum mandatory data for a missing/found person registration? The API requires none. |
| Q-4 | Is SMS/WhatsApp family notification in launch scope, and which provider and DLT templates will be used? |
| Q-5 | Will the frontend be served from the same origin as the API? |
| Q-6 | Will production use `MFA_MODE=totp`? |
| Q-7 | Do help desks need offline registration? No sync API exists. |
| Q-8 | Which UI languages are required? Message templates support `en` and `hi` only. |
| Q-9 | What are the staging and production hostnames? None are defined in the repository. |
| Q-10 | Is there an official item category taxonomy (needed for G-5)? |
| Q-11 | Are the default retention periods approved by the data protection owner? |
