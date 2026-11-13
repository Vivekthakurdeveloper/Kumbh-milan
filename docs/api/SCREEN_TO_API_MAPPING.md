# Screen-to-API mapping

Maps each expected frontend screen to the existing backend endpoints. No frontend exists in the repository, so screen names describe the functions the product needs rather than existing designs. All paths are relative to `/api/v1`. `{type}` is one of `missing-persons`, `found-persons`, `lost-items`, `found-items`.

**Readiness:**

| Rating | Meaning |
|---|---|
| **Ready** | Every action the screen needs is supported by an existing endpoint. |
| **Partial** | The core flow works; some expected capabilities are missing (listed with gap IDs from [API_GAP_ANALYSIS.md](./API_GAP_ANALYSIS.md)). |
| **Blocked** | The screen's main purpose cannot be delivered with the current backend. |

Roles: **HD** = `HELP_DESK_OPERATOR`, **PO** = `POLICE_OFFICER`, **AD** = `ADMIN`.

## Summary

| # | Screen | Roles | Readiness |
|---|---|---|---|
| 1 | Login | All | Ready |
| 2 | App shell, profile, change password | All | Ready |
| 3 | Dashboard | All | Partial |
| 4 | Case lists (per type) | All | Ready |
| 5 | Case registration (four forms) | All | Ready |
| 6 | Case details | All | Ready (names via `/users/directory`) |
| 7 | Edit case | All | Ready |
| 8 | Assign / reassign | PO, AD | Partial |
| 9 | Status actions (review, close, resolve, reopen) | All (by permission) | Ready |
| 10 | Matching: per-case candidates and review | All; review PO, AD | Ready |
| 11 | Matching: review queue across cases | PO, AD | Ready |
| 12 | Reunification / handover | PO, AD | Partial |
| 13 | Found-item custody | PO, AD | Partial |
| 14 | Global search | All | Ready |
| 15 | Map and nearby cases | All | Ready |
| 16 | Family notifications (SMS/WhatsApp) | All | Blocked |
| 17 | Staff in-app notifications | All | Blocked (no API) |
| 18 | Reports / CSV export | All | Partial |
| 19 | Audit log | AD | Partial |
| 20 | User management | AD | Ready |
| 21 | System settings: stations and locations | AD | Partial |
| 22 | Retention and legal holds | AD | Ready |

Totals: 12 Ready, 8 Partial, 2 Blocked.

## 1. Login

| Aspect | Detail |
|---|---|
| Roles | Unauthenticated |
| APIs | `POST /auth/login`, then `GET /auth/me` |
| Actions | Sign in |
| Form fields | `username` (1–64; trimmed, case-insensitive), `password` (1–128) |
| Data deps | None |
| UI states | Submitting; `401 invalid_credentials` ("Incorrect username or password"; the same message for unknown and inactive users); `429 account_temporarily_locked` (5 failures in 15 min, locked 15 min by default); `429 too_many_requests` (30/min per IP) with `Retry-After` |
| Missing APIs | Staff still use admin reset. PUBLIC citizens use `POST /auth/forgot-password` + `POST /auth/reset-password` (SMS delivery depends on notification provider). |
| Readiness | **Ready** |

## 2a. Forgot password (login page)

| Aspect | Detail |
|---|---|
| Roles | None (signed out) |
| APIs | `POST /auth/forgot-password` then `POST /auth/reset-password` (not the admin route `POST /users/{id}/reset-password`) |
| Actions | Enter username, email, or mobile; enter the code and a new password; return to login |
| Form fields | Step 1: `identifier`, optional `channel` (`EMAIL`/`SMS`). Step 2: `identifier`, `code` (digits, length from `code_length`), `new_password` |
| UI states | Step 1 always shows the same "check your email or phone" screen after `202`. `503 password_reset_unavailable`: tell the user to ask an administrator. Step 2: `422 invalid_reset_code` (request a new code), `422 weak_password` (code still valid), `429`. After `204`, all sessions are revoked: go to login |
| Missing APIs | None. SMS delivery needs a real provider; email works with `EMAIL_PROVIDER=smtp` |
| Readiness | **Ready** |

## 2. App shell, profile, change password

| Aspect | Detail |
|---|---|
| Roles | All |
| APIs | `GET /auth/me`, `GET /stations` (station name), `POST /auth/change-password`, `POST /auth/logout`, `POST /auth/refresh`; optional `POST /auth/mfa/enroll`, `POST /auth/mfa/confirm` |
| Actions | Show name, role, station; build menus from `permissions`; change password; log out |
| Form fields | Change password: `current_password`, `new_password` (12–128, at least three character classes, not containing the username) |
| Data deps | `permissions[]` drives every menu and button |
| UI states | After change-password (`204`) all sessions are revoked: send the user to login. `422 invalid_current_password`, `422 weak_password` |
| Missing APIs | No endpoint tells the UI whether MFA is enabled (G-18); call enroll and handle `409 mfa_disabled`. |
| Readiness | **Ready** |

## 3. Dashboard

| Aspect | Detail |
|---|---|
| Roles | All (counts are scoped to the caller's visibility) |
| APIs | `GET /dashboard/summary` (optional `from`/`to` for the Today / 7 days / 30 days selector; they change `urgent_active`, `unassigned_active`, `registered_today`, `pending_match_reviews`); drill-downs via `GET /cases` (e.g. `unassigned=true`, `urgency_level=CRITICAL`) |
| Actions | View counts; click through to filtered lists |
| Form fields | — |
| Data deps | Default missing keys in `by_status` / `active_by_type` to 0 |
| UI states | Loading tiles; stale indicator using `generated_at`; manual refresh (no realtime push, G-20) |
| Missing APIs | Breakdowns by location/sector/station/day, trend charts, recent activity feed, average time to reunify (G-8). "Pending match reviews" has a count but no list (G-1). |
| Readiness | **Partial** |

## 4. Case lists (per type)

| Aspect | Detail |
|---|---|
| Roles | All |
| APIs | `GET /cases/{type}` (full records, paginated), `GET /stations`, `GET /users/assignable-officers` (PO/AD, for assignee filter and names) |
| Actions | Filter, sort, paginate, open a case |
| Form fields (filters) | `q`, `case_number`, `status` (multi), `urgency_level`, `station_id`, `assigned_to_user_id`, `unassigned`, `location`, `created_from/to`, `event_from/to`, `include_archived`, `sort`; persons: `age_group`, `gender`, `language`; items: `item_category` |
| Data deps | Station names from `/stations`; assignee names (see G-3) |
| UI states | Skeleton; empty vs no-results; page size ≤100 |
| Missing APIs | Filter by `location_id`/sector or custody status (G-10); sort by urgency/event time (G-11). |
| Readiness | **Ready** (core list); filters above are enhancements |

## 5. Case registration (missing person, found person, lost item, found item)

| Aspect | Detail |
|---|---|
| Roles | All (`case:create`); admins must pick a station |
| APIs | `POST /cases/{type}` (with `Idempotency-Key`); `GET /locations`, `/locations/sectors`, `/locations/ghats` (place picker); `GET /stations` (admin); then `POST /cases/{id}/attachments` per photo/voice note; response includes automatic match suggestions → `GET /cases/{id}/matches` |
| Actions | Fill form, capture GPS or map pin, attach photos/voice, submit, see possible matches |
| Form fields | See the type-specific field tables in [API_REQUEST_RESPONSE_REFERENCE.md §6.1–6.2](./API_REQUEST_RESPONSE_REFERENCE.md). Common: `urgency_level`, `contact_details` (write-only), `location_id`, `latitude`/`longitude` + `coordinate_source` (+ `coordinate_accuracy_m`), `station_id` (admin only). Required: items need `item_category` + `item_name`; persons have no required fields (Q-3). |
| Data deps | Location reference data; item category list is free text (G-5) |
| UI states | Two-step submit (case, then uploads) with per-file progress; `202` upload = "scanning"; `422` field errors; `coordinates_out_of_area`; after success, show case number and any `HIGH` suggestions |
| Missing APIs | Item category reference list (G-5); duplicate-registration warning (G-12). |
| Readiness | **Ready** |

## 6. Case details

| Aspect | Detail |
|---|---|
| Roles | All (scoped; out-of-scope → 404) |
| APIs | `GET /cases/{id}` (or `/cases/{type}/{id}`), `GET /cases/{id}/history`, `GET /cases/{id}/notes`, `POST /cases/{id}/notes`, `GET /cases/{id}/attachments`, `GET /cases/{id}/attachments/{aid}` (view URL), `DELETE …/attachments/{aid}` (PO/AD), `GET /cases/{id}/matches`, `GET /cases/{id}/notifications`, `POST /cases/{id}/contact/reveal` (PO/AD), `GET /locations/{id}`, `GET /stations` |
| Actions | View details, timeline, notes (add, correct), media, matches; reveal contact with reason; open the edit, assign, status, custody actions |
| Form fields | Note: `note_text`, `note_type`, `is_sensitive` (PO/AD), `corrects_note_id` (for CORRECTION). Reveal: `reason_code`, optional `note` (≤200). Delete attachment: `reason` (3–2000). |
| Data deps | User names for reporter, assignee, note authors via `GET /users/directory?ids=…` |
| UI states | 404 "not found or not available to you"; `has_contact_details` controls the reveal button; sensitive notes and evidence fields are `null` for HD |
| Missing APIs | Custody history not visible to non-admins (G-7). |
| Readiness | **Ready** (core case details); custody history remains a gap |

## 7. Edit case

| Aspect | Detail |
|---|---|
| Roles | All (scoped) |
| APIs | `PATCH /cases/{type}/{id}` with `expected_version` |
| Actions | Edit details, position, urgency, contact |
| Form fields | Same as create minus `station_id`; send only changed fields; `null` clears a field |
| Data deps | Current `version` from the loaded case |
| UI states | `409 case_changed` → reload and re-apply; `409 case_closed` for RESOLVED/CLOSED/archived; matching re-runs on save |
| Missing APIs | — |
| Readiness | **Ready** |

## 8. Assign / reassign

| Aspect | Detail |
|---|---|
| Roles | PO, AD (`case:assign`) |
| APIs | `GET /users/assignable-officers?station_id=`, `POST /cases/{type}/{id}/assign` |
| Actions | Pick officer, give reason, assign |
| Form fields | `assignee_user_id`, `reason` (optional, 3–2000), `expected_version` |
| Data deps | Officer list (police at the case's station; admins may pass `station_id`) |
| UI states | `409 already_assigned`, `case_inactive`, `case_changed`; `422 invalid_assignee` |
| Missing APIs | Unassign; assign to a team or help-desk user (G-4); supervisor approval; assignee notification (G-6). HD has no assign action at all (by design). |
| Readiness | **Partial** |

## 9. Status actions

| Aspect | Detail |
|---|---|
| Roles | `UNDER_REVIEW`: all; `CLOSED`/`RESOLVED`/`REOPENED`: PO, AD |
| APIs | `POST /cases/{type}/{id}/status` |
| Actions | Mark under review, close, resolve, reopen |
| Form fields | `status`, `reason` (required, 3–2000), `expected_version`; resolve: `match_id` **or** `verification_evidence` (10–5000) |
| Data deps | Allowed transitions table ([FRONTEND_INTEGRATION_GUIDE.md §12](./FRONTEND_INTEGRATION_GUIDE.md)); confirmed matches for resolve; `custody_status` for found items |
| UI states | `409 invalid_status_transition`, `assignee_required`, `custody_not_released`, `case_anonymized`; `422 verification_required`, `safeguarding_evidence_required` |
| Missing APIs | — |
| Readiness | **Ready** |

## 10. Matching: per-case candidates and review

| Aspect | Detail |
|---|---|
| Roles | View/propose/generate: all. Confirm/reject/revoke: PO, AD |
| APIs | `GET /cases/{id}/matches`, `POST /cases/{id}/matches/generate`, `POST /cases/{id}/matches` (manual), `POST /cases/{id}/matches/{mid}/review`, `POST /cases/{id}/matches/{mid}/revoke` |
| Actions | Compare side by side using `counterpart` and `reasons`; propose; confirm/reject with evidence; revoke |
| Form fields | Propose: `other_case_id`, `rationale` (10–5000). Review: `decision` (`CONFIRMED`/`REJECTED`), `evidence` (10–5000), optional `left_case_version` / `right_case_version`. Revoke: `reason` (10–5000). |
| Data deps | Counterpart detail via `GET /cases/{counterpart.id}` when in scope |
| UI states | HD sees redacted rows (score/reasons `null`); `403 separation_of_duties`; `409 conflicting_confirmed_match`, `case_not_confirmable`, `match_already_reviewed` |
| Missing APIs | No photo comparison, matching is rule-based only. |
| Readiness | **Ready** |

## 11. Matching: review queue across cases

| Aspect | Detail |
|---|---|
| Roles | All (`case:read`); confirm/reject PO, AD |
| APIs | `GET /matches?status=PROPOSED&confidence_band=&match_domain=&limit=&offset=` → `Page[MatchQueueItem]`; review via `POST /cases/{id}/matches/{mid}/review`; count via `GET /dashboard/summary` (`pending_match_reviews`) |
| Actions | Browse proposed matches with both case summaries; open either case; confirm or reject |
| Missing APIs | — |
| Readiness | **Ready** |

## 12. Reunification / handover

| Aspect | Detail |
|---|---|
| Roles | PO, AD |
| APIs | Confirm match (10), then `POST /cases/{type}/{id}/status` → `RESOLVED` with `match_id` or `verification_evidence`; `POST /cases/{id}/contact/reveal` (`reason_code: CARE_HANDOVER`); optional family message `POST /cases/{id}/notifications` (blocked, see 16); notes for details |
| Actions | Verify identity, record evidence, resolve both cases |
| Form fields | `verification_evidence` text; no structured claimant fields |
| Missing APIs | No claimant/collector record (name, relationship, ID document type/number, signature or photo, handover time) and no reunification entity tying both cases (G-2). Evidence lives in free text. |
| Readiness | **Partial** |

## 13. Found-item custody

| Aspect | Detail |
|---|---|
| Roles | View: all. Change: PO, AD (`case:custody_update`) |
| APIs | `POST /cases/found-items/{id}/custody`; then status `RESOLVED` once `RELEASED` |
| Actions | Transfer to a storage location; release to owner |
| Form fields | `custody_status`, `storage_location` (required for TRANSFERRED), `recipient_acknowledgement` (required for RELEASED, 10–500), `reason` (required, 3–2000). No `expected_version` on this request. |
| Data deps | Current `details.custody_status` |
| UI states | `409 invalid_custody_transition` (RELEASED is terminal), `case_closed`; `422 storage_location_required`, `handover_required` |
| Missing APIs | Custody chain history for non-admins (G-7); structured claimant record (G-2). |
| Readiness | **Partial** |

## 14. Global search

| Aspect | Detail |
|---|---|
| Roles | All; `all_stations=true` PO, AD |
| APIs | `GET /cases` (returns `CaseSummary`) |
| Form fields | `q`, `case_number`, `case_type`, `status`, filters as in 4, `all_stations` |
| UI states | `in_scope: false` rows are read-only (opening returns 404); `location_id` is always `null` in results (G-14) |
| Missing APIs | — (filters G-10/G-11 as enhancements) |
| Readiness | **Ready** |

## 15. Map and nearby cases

| Aspect | Detail |
|---|---|
| Roles | All; `all_stations` PO, AD |
| APIs | `GET /cases/map?bbox=…`, `GET /cases/nearby?latitude=&longitude=&radius_m=`, `GET /locations` |
| UI states | `422 invalid_bbox`, `bbox_too_large`, `radius_too_large`; markers carry no personal data |
| Missing APIs | — |
| Readiness | **Ready** |

## 16. Family notifications (SMS / WhatsApp)

| Aspect | Detail |
|---|---|
| Roles | All |
| APIs | `POST /cases/{id}/notifications` (202, queued), `GET /cases/{id}/notifications` |
| Form fields | `template`, `channel` (default SMS), `locale` (`en`/`hi`, default `hi`), `recipient_phone` (8–20), `consent_statement` (10–500) (see reference §9) |
| UI states | Status `PENDING` / `SENT` / `FAILED` |
| Blocker | No messaging provider is integrated. In staging and production the provider must be disabled, so messages stay `PENDING` and are never delivered (G-9). The screen can be built, but it must not tell users a message was sent. |
| Readiness | **Blocked** |

## 17. Staff in-app notifications

| Aspect | Detail |
|---|---|
| APIs | None |
| Missing APIs | No staff notification feed, unread counts, push, email, or realtime channel (G-19, G-20). |
| Readiness | **Blocked** (do not build) |

## 18. Reports / CSV export

| Aspect | Detail |
|---|---|
| Roles | All (scoped) |
| APIs | `GET /reports/cases.csv?created_from=&created_to=` (no other filters) |
| UI states | `422 invalid_range`, `range_too_large` (>31 days); truncated at 1000 rows; archived cases excluded. `X-Export-Truncated` and filename are unreadable cross-origin (G-16): deploy same-origin or always warn. |
| Missing APIs | Other report types, aggregates, PDF, scheduled reports (G-21). |
| Readiness | **Partial** |

## 19. Audit log

| Aspect | Detail |
|---|---|
| Roles | AD only (`audit:read`) |
| APIs | `GET /audit-events` (filters per reference §12) |
| Missing APIs | A per-case activity timeline for PO/HD beyond `/history` (G-22). Do not expose this screen to other roles. |
| Readiness | **Partial** (admin screen ready; non-admin timeline missing) |

## 20. User management

| Aspect | Detail |
|---|---|
| Roles | AD |
| APIs | `GET /users`, `POST /users`, `GET /users/{id}`, `PATCH /users/{id}`, `POST /users/{id}/reset-password`, `GET /stations` |
| Form fields | `username`, `full_name`, `password`, `role`, `station_id` (required for PO/HD), `police_badge_number` (required for PO), `email`, `phone_number`, `is_active` |
| UI states | `403 self_lockout`; `409 duplicate`; reset revokes the user's sessions; step-up header needed only when `MFA_MODE=totp` (G-17) |
| Readiness | **Ready** |

## 21. System settings: stations and locations

| Aspect | Detail |
|---|---|
| Roles | AD |
| APIs | `GET/POST /stations`, `PATCH /stations/{id}`; `GET/POST /locations`, `PATCH /locations/{id}` |
| Missing APIs | No API for runtime configuration (retention periods, matching thresholds, geofence, rate limits): these are environment variables. No item-category management (G-5). |
| Readiness | **Partial** |

## 22. Retention and legal holds

| Aspect | Detail |
|---|---|
| Roles | AD |
| APIs | `GET /retention/policies`, `POST/GET /retention/runs`, `GET /retention/runs/{id}`, `POST …/approve`, `…/cancel`, `…/execute`; `GET/POST /cases/{id}/legal-holds`, `POST …/legal-holds/{hid}/release` |
| UI states | Two-person rule (`409 two_person_rule`): the planner cannot approve; anonymisation in staging/production must be executed by the retention task (`409 execute_via_retention_task`) |
| Readiness | **Ready** |
