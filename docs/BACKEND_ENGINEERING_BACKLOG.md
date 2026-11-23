# Backend engineering backlog

Baseline: `main` at `a60e8c3` on 23 November 2026. Updated with schema revision `0017` (operations modules).

This backlog covers only backend work. Web, Android, and iOS screens are client-team deliverables. Their shared authentication, case, media, public, and synchronization APIs already exist.

## Priority and status

- **P0:** Blocks a supported client flow or creates a release defect.
- **P1:** Required for an operationally complete launch.
- **P2:** Important product or operational enhancement.
- **P3:** Optional or future capability.
- **READY:** Engineering can start with the current requirements.
- **DECISION REQUIRED:** Product, legal, security, or an external provider must first supply a named decision or contract.

## Delivery rules for every task

- Add an Alembic migration for every schema change, including runtime-role grants and a tested downgrade.
- Enforce RBAC and row visibility in the service layer; an out-of-scope resource must not leak its existence.
- Redact contact details, identity documents, credentials, provider payloads, and biometric data from logs and audit payloads.
- Make externally visible writes idempotent and safe to retry.
- Add unit, API, authorization, migration, and failure-path tests as applicable.
- Update generated OpenAPI and the API request/response reference.
- Add metrics and structured logs for workers and external calls.
- Keep third-party integrations behind interfaces so providers can be replaced.

---

## Milestone A — Core release closure

### BE-001 — Fix browser CORS contracts

**Priority:** P0  
**Status:** IMPLEMENTED AND TESTED
**Size:** S

**Outcome**

Cross-origin clients can read export metadata and send a TOTP step-up code.

**Scope**

- Expose `Content-Disposition`, `X-Export-Rows`, and `X-Export-Truncated`.
- Allow the `X-Step-Up-Code` request header.
- Preserve the existing strict origin allowlist.

**Acceptance criteria**

- [x] A permitted browser origin can read all three export headers.
- [x] TOTP-protected password reset and retention execution pass preflight.
- [x] An unapproved origin remains blocked.
- [x] Middleware and CORS tests cover both positive and negative cases.

Verification: `tests/test_cors_contract.py` passes without a database.

---

### BE-002 — Add structured reunification and handover records

**Priority:** P1  
**Status:** IMPLEMENTED AND TESTED; CLIENT/POLICY ROLLOUT PENDING
**Size:** L  
**Depends on:** Production claimant/document-retention policy and safeguarding sign-off before enabling the resolution gate

**Outcome**

Person reunification and item return are represented by structured, auditable records rather than only free text.

**Scope**

- Add an encrypted handover/reunification model linked to both cases and the confirmed match, with append-only lifecycle audit history.
- Store approved claimant fields such as name, relationship, document type/reference, handover time, and verification method.
- Encrypt sensitive claimant fields and never return them in list/search responses.
- Add scoped create and read endpoints under `/api/v1/cases/{case_id}/handovers`.
- Require an appropriate handover before resolving person cases or completing an item return, according to the approved policy.
- Audit create, verify/reject, and completion actions with redacted metadata.

**Acceptance criteria**

- [ ] Police/admin can record a handover for an in-scope case.
- [ ] Help-desk and PUBLIC users cannot reveal claimant identity fields.
- [ ] Invalid match, case type, status, or duplicate completion is rejected.
- [ ] Resolution without the required handover returns a documented `422`.
- [ ] Concurrent retries create only one record.
- [ ] Retention and legal-hold processing includes the new records.

Implementation note: revision `0014` adds encrypted structured handovers, police/admin permissions, create/list/detail/verify/reject/complete/history APIs, duplicate and custody gates, retention anonymization, and the rollout flag `HANDOVER_REQUIRED_FOR_RESOLVE`. The full isolated suite passes.

---

### BE-003 — Expose a complete custody history

**Priority:** P1  
**Status:** IMPLEMENTED AND TESTED
**Size:** M

**Outcome**

Authorized case handlers can see every found-item custody transfer and release.

**Scope**

- Persist immutable custody events with actor, timestamp, previous/new state, storage location, reason, and a handover-evidence marker. Link the structured handover after BE-002 exists.
- Include custody events in `GET /api/v1/cases/{case_id}/history` or expose a dedicated endpoint.
- Keep claimant details redacted unless the caller has contact-reveal permission.
- Define migration behavior for existing found-item cases; do not invent historical events.

**Acceptance criteria**

- [ ] Two transfers and one release produce three chronologically ordered events.
- [ ] Police/admin can read events for in-scope cases.
- [ ] Help-desk visibility follows the approved custody policy.
- [ ] Events cannot be updated, deleted, or truncated by the application role.
- [ ] History remains intact after a case is closed.

Implementation note: new custody changes enrich the existing append-only audit event and `CaseHistory.custody_history` projects it for scoped staff callers. Existing events are not backfilled with invented location data. The full isolated suite passes.

---

### BE-004 — Integrate a production messaging provider

**Priority:** P1, or P0 when family messaging is launch-critical  
**Status:** IMPLEMENTED AND TESTED; LIVE MSG91 CREDENTIALS AND DLT TEMPLATES PENDING  
**Size:** L  
**Depends on:** Selected provider, DLT registration/templates, consent policy, sandbox credentials

**Outcome**

Queued SMS/WhatsApp messages reach a real provider and delivery state is reconciled.

**Scope**

- Implement the provider behind the existing notification interface.
- Add provider idempotency keys, bounded retries, timeouts, and error classification.
- Validate approved English/Hindi template identifiers.
- Add signed delivery-receipt webhooks with replay protection.
- Reconcile `PENDING`, `SENT`, `DELIVERED`, and terminal failure states.
- Add queue-age, delivery-rate, retry, and provider-error metrics.

**Acceptance criteria**

- [ ] A sandbox message moves from `PENDING` to `SENT` and then `DELIVERED`.
- [ ] Duplicate worker execution or webhook delivery has no duplicate effect.
- [ ] Invalid webhook signatures are rejected.
- [ ] Provider downtime leaves a visible retryable or failed state.
- [ ] No phone number or message body appears in logs, metrics, or unredacted audit data.

Implementation note (revision `0017`): MSG91 sending already existed. Added `DELIVERED` status, `POST /api/v1/webhooks/msg91/delivery` authenticated with `X-Webhook-Token` (`MSG91_WEBHOOK_SECRET`), idempotent receipt reconciliation (duplicates and late receipts never move a message backwards), receipt counters, and queue-age / awaiting-receipt gauges. Remaining: MSG91 auth key, sender id, approved English/Hindi DLT template ids, and a sandbox run.

---

### BE-005 — Build grievance management

**Priority:** P1 when included in launch scope  
**Status:** IMPLEMENTED AND TESTED (DEFAULT POLICY)  
**Size:** XL  
**Depends on:** Categories, statuses, SLA rules, escalation matrix, citizen identity model

**Outcome**

Citizens can submit and track grievances, while authorized staff can assign, investigate, escalate, and resolve them.

**Child tasks**

1. **BE-005A — Grievance domain and permissions**
   - Add grievance, status-history, assignment, comment, and attachment relationships.
   - Define citizen, help-desk, police, and admin permissions and station scope.
2. **BE-005B — Citizen submission and tracking**
   - Support OTP/status-token and authenticated PUBLIC-account flows.
   - Return non-enumerating acknowledgements and privacy-safe status.
3. **BE-005C — Staff workflow**
   - Add queues, assignment, internal notes, evidence, status transitions, closure, and reopen.
4. **BE-005D — SLA and escalation**
   - Calculate due times, run escalation jobs, notify assignees, and expose overdue counts.
5. **BE-005E — Grievance reporting**
   - Provide scoped aggregates and bounded export without sensitive text.

**Acceptance criteria**

- [ ] Citizens cannot enumerate or access another citizen's grievance.
- [ ] Every status and assignment change is immutable and audited.
- [ ] SLA calculation uses a documented timezone and holiday policy.
- [ ] Attachments use the existing private storage and malware-scanning pipeline.
- [ ] Retention and legal holds cover grievances and their attachments.

Implementation note: categories SERVICE / STAFF_CONDUCT / DELAY / OTHER; statuses OPEN → ASSIGNED → IN_PROGRESS → RESOLVED → CLOSED with REOPENED; 72-hour SLA (`GRIEVANCE_SLA_HOURS`), escalation to administrators and the assignee by the operations worker, Asia/Kolkata display, no holiday calendar. Citizens use a `GRIEVANCE` OTP token or a PUBLIC account (`/api/v1/public/grievances`); staff use `/api/v1/grievances`. Description, mobile, comments, and resolution are encrypted; history is append-only. Not yet done: grievance attachments and retention/legal-hold execution for grievances.

---

## Milestone B — Client API completion

### BE-101 — Complete account-free citizen intake

**Priority:** P2  
**Status:** IMPLEMENTED AND TESTED
**Size:** M  
**Depends on:** Approved public intake matrix — all four case types are approved by the current product plan

**Outcome**

The OTP portal supports every case type approved for anonymous/account-free submission.

**Current boundary**

Account-free APIs support missing-person, found-person, lost-item, and found-item reports plus sightings. Authenticated PUBLIC accounts can also create all four case types.

**Scope**

- Implement the approved found-person, lost-item, and found-item forms using the existing verified-mobile report token.
- Route submissions to the configured intake station with `PENDING_VERIFICATION`.
- Reuse idempotency, media validation, privacy controls, and staff review.

**Acceptance criteria**

- [ ] Each approved type has a tested submit, replay, review, and rejection flow.
- [ ] Public responses never expose internal notes, contacts, matches, or staff identities.

Implementation note: all four multipart case-report routes share OTP/mobile verification, required idempotency, intake-station routing, notification enqueueing, optional private photo upload, and staff review. The full isolated suite passes.

---

### BE-102 — Add mobile sync tombstones and scope revocation

**Priority:** P2  
**Status:** IMPLEMENTED AND TESTED
**Size:** M

**Outcome**

Offline clients learn when a previously visible case is archived, deleted from their local scope, or otherwise no longer readable.

**Scope**

- Extend the pull cursor protocol with tombstone and scope-revocation records.
- Preserve stable ordering and lag-window safety.
- Define client acknowledgement and replay behavior.

**Acceptance criteria**

- [x] A device receives a tombstone exactly once per cursor progression.
- [x] Retrying an old cursor returns the same deterministic result.
- [x] A scope change removes local access without revealing the case's new owner or station.

Implementation note: revision `0015` adds a per-user delivered-case ledger and append-only `ARCHIVED` / `SCOPE_REVOKED` stream with independent keyset cursor and pull-time scope reconciliation.

---

### BE-103 — Add durable offline attachment synchronization

**Priority:** P2  
**Status:** IMPLEMENTED AND TESTED
**Size:** L

**Outcome**

Mobile clients can safely resume media upload after reconnecting without embedding binary data in the JSON sync batch.

**Scope**

- Add an upload-session or attachment-manifest protocol.
- Support resumable retry, checksum verification, client record identity, expiry, and finalization.
- Preserve quarantine and malware-scan behavior.
- Expose attachment synchronization status through the existing sync surface.

**Acceptance criteria**

- [x] An interrupted upload resumes without creating a duplicate attachment.
- [x] Finalization fails when the checksum or size differs.
- [x] Unscanned media never receives a downloadable URL.
- [x] Expired incomplete sessions are safely purged.

Implementation note: revision `0016` adds bounded upload manifests, exact-offset chunk append, SHA-256/size finalization, existing quarantine/scan reuse, durable replay, status visibility, abort, and maintenance expiry cleanup.

---

### BE-104 — Complete assignment workflow

**Priority:** P2  
**Status:** IMPLEMENTED AND TESTED  
**Size:** M  
**Depends on:** Whether teams, supervisors, or help-desk assignees are valid

**Scope**

- Add unassign with an immutable reason.
- Add approved team/help-desk assignment types.
- Add optional bulk assignment and supervisor approval.
- Notify the assignee through the existing staff notification feed.

**Acceptance criteria**

- [ ] Unassign returns the case to the approved workflow state.
- [ ] Every change appears in assignment history.
- [ ] Station scope and optimistic version checks remain enforced.

Implementation note: officers and same-desk help-desk operators are valid assignees (teams are not modelled). `ASSIGNMENT_REQUIRES_APPROVAL=true` (default) turns non-admin assignment into a `202` pending request that an ADMIN supervisor approves under `/api/v1/assignment-requests`; self-approval is blocked. Added unassign with reason, bulk assignment (`/api/v1/cases/bulk-assign`), and staff notifications.

---

### BE-105 — Add reference data, filters, sorting, and duplicate detection

**Priority:** P2  
**Status:** IMPLEMENTED AND TESTED  
**Size:** L  
**Depends on:** Approved item taxonomy and duplicate policy

**Scope**

- Add managed item-category and localized enum-reference APIs.
- Add filters for location, sector, custody status, reporter, and contact presence.
- Add urgency and event-time sorting.
- Detect likely same-type duplicate registrations without automatically merging cases.

**Acceptance criteria**

- [ ] Reference values have stable codes and English/Hindi labels.
- [ ] New filters and sorts are indexed and preserve row-level scope.
- [ ] Duplicate candidates include explainable reasons and require human action.

Implementation note: managed `item_categories` (seeded, admin-editable, free text still accepted), `/api/v1/public/reference/enums` with English/Hindi labels, filters `custody_status`, `reported_by_user_id`, `has_contact`, `location_id`, `sector_id`, sorts `±urgency` and `±event_at` with indexes, and `GET /api/v1/cases/{id}/duplicates` returning explainable candidates without merging.

---

### BE-106 — Add realtime staff updates

**Priority:** P3  
**Status:** IMPLEMENTED AND TESTED (SSE)  
**Size:** L  
**Depends on:** SSE versus WebSocket decision and deployment topology

**Scope**

- Stream case, assignment, match, notification, and grievance events.
- Authorize every subscription and re-check access after role/scope changes.
- Support reconnection with a bounded event cursor.

**Acceptance criteria**

- [ ] A disconnected client can resume without missing or duplicating visible events.
- [ ] A user never receives an event for an out-of-scope case.
- [ ] The API remains usable when the realtime channel is unavailable.

Implementation note: `GET /api/v1/events/stream` (Server-Sent Events, resumable with `Last-Event-ID`) and `GET /api/v1/events` (cursor poll fallback). Events are derived from the audit log, carry identifiers only, and are re-authorized every poll. Staff-notification events are not streamed yet.

---

## Milestone C — Dashboard, MIS, and analytics

### BE-201 — Expand operational dashboard aggregates

**Priority:** P2  
**Status:** IMPLEMENTED AND TESTED (METRICS AS LISTED)  
**Size:** L  
**Depends on:** BE-002 for authoritative reunification metrics

**Scope**

- Add breakdowns by station, sector, location, case type, urgency, and day.
- Add median time to assignment, match, reunification/return, and closure.
- Add unresolved-age buckets, failed-notification counts, SLA counts, and recent activity.
- Use scoped aggregate queries and add required indexes.

**Acceptance criteria**

- [ ] Every aggregate equals an equivalent scoped case query on the same test fixture.
- [ ] Date boundaries use Asia/Kolkata consistently.
- [ ] Out-of-scope stations and cases never influence a caller's response.
- [ ] Agreed performance targets pass on representative test data.

Implementation note: `GET /api/v1/dashboard/analytics` — breakdowns by station, sector, type, urgency, status, and Asia/Kolkata day; median seconds to assignment, confirmed match, reunification/return, and closure; unresolved-age buckets; failed notifications; grievance open/overdue; recent activity. Performance targets on representative data are not yet measured.

---

### BE-202 — Add MIS reports and scheduled exports

**Priority:** P2  
**Status:** IMPLEMENTED AND TESTED (CSV)  
**Size:** L  
**Depends on:** Approved report catalogue and recipients

**Scope**

- Add filtered case, reunification, custody, grievance, and notification reports.
- Support asynchronous generation for large CSV/PDF outputs.
- Store generated artifacts privately with expiry.
- Add schedules, approved recipients, audit, and cancellation.

**Acceptance criteria**

- [ ] Large reports do not block API workers.
- [ ] Exports apply spreadsheet-injection protection and exclude unapproved PII.
- [ ] Download links are scoped, audited, short-lived, and revocable.

Implementation note: `/api/v1/reports/jobs` (CASES, REUNIFICATIONS, CUSTODY, GRIEVANCES, NOTIFICATIONS) generated by `python -m app.cli operations-worker`, stored privately, downloaded through an audited endpoint, expiring after `REPORT_ARTIFACT_TTL_HOURS`, revocable by cancel. Daily/weekly schedules with validated recipients notify in-app. PDF output is not implemented.

---

## Milestone D — External channels and systems

### BE-301 — Define a common outbound integration framework

**Priority:** P1 before channel integrations  
**Status:** IMPLEMENTED AND TESTED (MOCK + SIGNED WEBHOOK ADAPTERS)  
**Size:** L  
**Depends on:** Protocol and security contracts from PA, VMD, social, and government-system owners

**Outcome**

PA, LED/VMD, social, and government adapters share reliable delivery, security, and observability behavior.

**Scope**

- Define a provider interface, transactional outbox, retry policy, dead-letter state, and reconciliation model.
- Add per-destination credentials, allowlists, rate limits, timeouts, and circuit breaking.
- Define payload classification so public channels never receive contact, biometric, or sensitive case data.
- Add signed inbound webhook handling where required.

**Acceptance criteria**

- [ ] A mock adapter proves success, retry, timeout, terminal failure, and reconciliation paths.
- [ ] Duplicate dispatch cannot publish twice when the destination supports idempotency.
- [ ] Operators can identify failed deliveries without viewing sensitive payloads.

Implementation note: `integration_destinations` registry, `outbound_dispatches` outbox with idempotency keys, exponential retry, per-destination circuit breaker and health, host allowlist (`INTEGRATION_ALLOWED_HOSTS`), HMAC-signed webhook adapter, payload policy that rejects phone numbers / emails / ID numbers, and signed inbound messages quarantined for review (`/api/v1/webhooks/integrations/{code}`).

---

### BE-302 — Public Address System integration

**Priority:** P1 when contractually required  
**Status:** FRAMEWORK READY; VENDOR ADAPTER PENDING  
**Size:** L  
**Depends on:** BE-301 and named PA vendor/protocol

**Scope**

- Register PA destinations/zones and their health state.
- Add approved multilingual announcement templates.
- Create, approve, schedule, cancel, and dispatch announcements.
- Store acknowledgement or failure returned by the PA system.
- Prevent names, phone numbers, document numbers, or biometric data from entering announcements.

**Acceptance criteria**

- [ ] Only authorized staff can approve and dispatch.
- [ ] The same request cannot play twice after a retry.
- [ ] Every dispatch, cancellation, acknowledgement, and failure is audited.

Implementation note: PA zones are `PUBLIC_ADDRESS` destinations; announcements are bilingual public alerts with second-person approval, scheduling, withdrawal, and audited dispatch. A vendor-specific adapter is needed once the PA vendor and protocol are named.

---

### BE-303 — LED/VMD display integration

**Priority:** P1 when contractually required  
**Status:** FRAMEWORK READY; VENDOR ADAPTER PENDING  
**Size:** L  
**Depends on:** BE-301 and named display platform/protocol

**Scope**

- Register displays, groups, locations, capabilities, and health.
- Publish approved multilingual content with schedule, priority, and expiry.
- Reconcile what content is currently active.
- Automatically remove expired or withdrawn alerts.

**Acceptance criteria**

- [ ] Content is delivered only to selected displays/zones.
- [ ] Expired content is no longer active.
- [ ] Payload policy prevents publication of sensitive case fields.

Implementation note: displays are `DISPLAY` destinations; alerts carry priority, start, and expiry; the worker expires alerts and sends withdrawals; `GET /api/v1/integrations/destinations/{id}/active-alerts` reconciles live content. A vendor adapter is needed once the display platform is named.

---

### BE-304 — Social media integration

**Priority:** P2 when approved  
**Status:** FRAMEWORK READY; PLATFORM ACCOUNTS PENDING  
**Size:** L  
**Depends on:** BE-301, approved platforms/accounts, moderation and consent policy

**Scope**

- Publish approved, redacted public alerts through platform-specific adapters.
- Support draft, approval, publish, update, withdraw, and provider-status reconciliation.
- Store platform post identifiers and links.
- Treat inbound comments/messages as untrusted; route them through moderation before creating sightings or notes.

**Acceptance criteria**

- [ ] Publishing requires explicit authorization and consent evidence.
- [ ] Withdrawal updates or removes the remote post when the provider allows it.
- [ ] Provider tokens and inbound content are never written to logs.

Implementation note: social accounts are `SOCIAL` destinations; case-linked posts require `consent_reference`; inbound comments arrive as quarantined inbound messages. Platform adapters (X / Facebook / Instagram / YouTube) need official accounts and API credentials.

---

### BE-305 — External government-system integrations

**Priority:** P1 when contractually required  
**Status:** FRAMEWORK READY; SYSTEM CONTRACTS PENDING  
**Size:** XL per system  
**Depends on:** A named system, data contract, legal basis, network path, credentials, and test environment

**Scope**

- Create one adapter and mapping specification per approved external system.
- Define source-of-truth, create/update ownership, conflict resolution, and replay behavior.
- Support signed webhooks or bounded polling, as specified by the external system.
- Add quarantine/dead-letter handling for invalid records.
- Record consent/legal basis and an auditable disclosure event for exported data.

**Acceptance criteria**

- [ ] Contract tests pass against the system's sandbox or certified emulator.
- [ ] Retries are idempotent and cannot create duplicate cases.
- [ ] Schema changes or unavailable dependencies fail visibly without corrupting local cases.
- [ ] No integration is enabled without an explicit destination allowlist.

Implementation note: `GOVERNMENT` destinations for CCTNS, ERSS_112, TRACKCHILD, KHOYA_PAYA, or GENERIC. `POST .../case-exports` sends a minimal case summary with a mandatory legal basis, idempotent per case version. Real mappings need each system's API specification, sandbox, network path, and credentials.

---

## Milestone E — Multilingual AI chatbot

### BE-401 — Approve chatbot safety and data contract

**Priority:** P1 before chatbot implementation  
**Status:** DEFAULT CONTRACT IMPLEMENTED; FORMAL ADR AND RED-TEAM PENDING  
**Size:** M discovery

**Required decisions**

- Supported languages and channels.
- Approved model/provider and data residency.
- Whether prompts may leave the deployment boundary.
- Approved intents and authoritative knowledge sources.
- Citizen identity method for case-specific status.
- Human handoff and emergency escalation behavior.
- Retention, consent, abuse, and red-team requirements.

**Deliverable**

An approved ADR, threat model, evaluation set, and API contract. No production chatbot should be built before these are accepted.

Implementation note: the safety contract is documented in `app/services/chatbot.py`: deterministic approved intents, emergencies always point to 112 and hand off, case status only with the OTP case-status token, model limited to published public information with no tools, redacted transcripts purged after 30 days.

---

### BE-402 — Implement multilingual chatbot backend

**Priority:** P2  
**Status:** IMPLEMENTED AND TESTED (MOCK MODEL)  
**Size:** XL  
**Depends on:** BE-401

**Scope**

- Add conversation/session APIs with rate limits and abuse controls.
- Route approved intents to deterministic services for public information, help-desk locations, report guidance, and OTP-protected case status.
- Ground generated answers only in approved, versioned content.
- Add language detection/selection, model-provider abstraction, human handoff, and emergency fallback.
- Store minimal redacted transcripts according to the approved retention policy.
- Add prompt-injection, data-exfiltration, unsafe-answer, and multilingual quality tests.

**Acceptance criteria**

- [ ] The model cannot access arbitrary case data or call unrestricted internal APIs.
- [ ] Case-specific responses require the existing verified citizen authorization.
- [ ] Unsupported or high-risk requests are handed off instead of guessed.
- [ ] Evaluation meets approved accuracy, safety, latency, and language thresholds.

Implementation note: `/api/v1/public/chat/sessions` (web, app, WhatsApp channel value), English/Hindi deterministic replies, 12 accepted locales via the model, staff handoff queue under `/api/v1/chat/sessions`. `CHAT_LLM_PROVIDER=openai_compatible` targets a self-hosted endpoint; it needs the GPU server, model choice, and an evaluation set. WhatsApp inbound delivery is not wired.

---

## Milestone F — Security and production hardening

### BE-501 — Add encryption-key rotation

**Priority:** P1  
**Status:** READY after key-management design approval  
**Size:** L

**Scope**

- Add key identifiers and dual-read/single-write support for contact and face-embedding ciphertext.
- Add resumable re-encryption jobs with progress, rollback, and audit.
- Keep old keys available until verification completes.

**Acceptance criteria**

- [ ] New data uses the active key while old data remains readable.
- [ ] Re-encryption can stop and resume without loss or double processing.
- [ ] Key material never enters the database, logs, metrics, or audit payloads.

---

### BE-502 — Add PostgreSQL row-level security

**Priority:** P2 defense in depth  
**Status:** READY after policy review  
**Size:** XL

**Scope**

- Add policies for staff station scope and PUBLIC ownership where technically appropriate.
- Set trusted request identity/role context per transaction.
- Preserve background-worker and migration access through separate roles.
- Keep application-layer scope checks.

**Acceptance criteria**

- [ ] Direct app-role SQL cannot read another station's or citizen's protected rows.
- [ ] Connection pooling cannot leak identity context between requests.
- [ ] Existing authorization and worker tests continue to pass.

---

### BE-503 — Productionize MFA

**Priority:** P1  
**Status:** DECISION REQUIRED  
**Size:** L  
**Depends on:** Approved phishing-resistant factor, enrollment, recovery, and support process

**Acceptance criteria**

- [ ] Privileged actions require a recently verified approved factor.
- [ ] Enrollment and recovery cannot be completed with only a stolen password.
- [ ] Clients can discover MFA mode and enrollment state through `/auth/me`.
- [ ] Recovery and administrator override are audited and tested.

---

### BE-504 — Deploy and verify malware scanning

**Priority:** P1  
**Status:** READY  
**Size:** M

**Scope**

- Add a supported ClamAV deployment to the approved environment.
- Configure health/readiness and worker supervision.
- Run the real EICAR integration test.
- Alert on queue age, scanner failures, and files stuck in quarantine.

**Acceptance criteria**

- [ ] Clean files become downloadable only after a successful scan.
- [ ] EICAR is rejected by a real daemon.
- [ ] Scanner outage fails closed and makes the dependency visible.

---

### BE-505 — Enforce CI gates and verify capacity and recovery

**Priority:** P1 before production  
**Status:** PARTLY READY; target environments require a decision  
**Size:** L

**Current evidence**

The hosted `backend` GitHub Actions workflow passes on current `main`. It compiles the application, runs pytest, and checks Alembic schema drift.

**Scope**

- Require the passing backend check through protected-branch rules.
- Add generated OpenAPI drift and an approved formatter/linter check.
- Define traffic and data-volume targets and execute load tests.
- Exercise database/object-storage backup and restore in an isolated environment.
- Record capacity, recovery time, and recovery point evidence.

**Acceptance criteria**

- [ ] A protected branch cannot merge when tests or migration checks fail.
- [ ] Agreed peak traffic passes documented latency and error thresholds.
- [ ] A restored environment passes schema, health, and smoke checks.

---

## Suggested implementation order

1. BE-001 browser contract fixes.
2. Approve and implement BE-002 structured handovers.
3. BE-003 custody history.
4. Select a messaging provider, then implement BE-004.
5. Decide grievance scope, then deliver BE-005 in child tasks.
6. Complete mobile/public APIs through BE-101 to BE-105.
7. Deliver analytics after structured handover data exists.
8. Approve BE-301 and implement only the contracted external channels.
9. Complete the chatbot safety contract before any model integration.
10. Run BE-501 through BE-505 before a production launch.

## Definition of done

A task is complete only when:

- [ ] Acceptance criteria and negative authorization cases pass.
- [ ] Migration upgrade/downgrade and app-role grants are tested.
- [ ] OpenAPI and API reference are updated.
- [ ] Audit redaction is verified.
- [ ] Metrics, structured errors, and operational runbooks are added.
- [ ] Full pytest passes, aside from explicitly documented environment-dependent skips.
- [ ] No unresolved P0/P1 security or privacy finding remains for the delivered feature.
