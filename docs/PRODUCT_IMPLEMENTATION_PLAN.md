# Kumbh Milan — Product Development and Implementation Plan

## Objective

Deliver a secure, production-ready Lost & Found, Incident Coordination, and Public Assistance platform for Haridwar Kumbh 2027.

Completion means that approved workflows work end to end across staff web, citizen web, mobile, backend, data, integrations, security, operations, and support. An API or screen is not complete merely because it returns a successful response.

## Verified baseline

Baseline reviewed: `main` at `a60e8c3` expected revision `0013`; the current programme worktree expects revision `0016`.

The repository currently contains:

- A FastAPI backend with 104 OpenAPI paths and 127 operations in the current worktree.
- PostgreSQL/PostGIS, Redis, private S3-compatible media storage, and background workers.
- Staff and PUBLIC authentication, four case types, workflow, matching, optional face-assisted candidates, public OTP intake, mobile synchronization, audit, retention, dashboard counts, and bounded CSV export.
- No web, Android, or iOS client source.
- No grievance, PA, LED/VMD, social-media, chatbot, voice-AI, or government-system modules.
- Existing roles: `ADMIN`, `POLICE_OFFICER`, `HELP_DESK_OPERATOR`, and `PUBLIC`.
- No approved volunteer or supervisor role.
- Hosted backend CI passes on current `main` with 379 tests passed and one environment-dependent ClamAV test skipped.

The worktree also contains pending case-history, validation, role-label, and password-reset changes documented in `PENDING_REQUIREMENTS_IMPLEMENTATION.md`. Those changes must be preserved and regression-tested before overlapping workflow work is merged.

## Programme corrections and guardrails

### The 12-week plan is a pilot target, not a full-programme promise

A 12-week delivery is realistic only for:

- Core backend closure.
- One staff web MVP.
- One citizen portal MVP.
- Mobile synchronization MVP.
- Basic MIS.
- Pilot security, deployment, and operational rehearsal.

PA, LED/VMD, social media, grievance, AI/voice, and government integrations depend on contracts, credentials, approved data-sharing rules, and external test environments. The complete programme should be planned as a 24–36 week multi-team delivery, subject to those dependencies.

### Security is continuous

Security cannot wait until Phase 7. Threat modelling, RBAC, privacy review, audit, encryption, secret management, and failure testing are required within every phase. Phase 7 is the final hardening and assurance gate.

### Production readiness is an exit gate

Phase 8 is not a feature sprint. It validates that all launch-scope features, clients, workers, infrastructure, procedures, and people are ready to operate together.

### Preserve the current architecture

Continue with the modular FastAPI application and supervised workers. Introduce separate services only when independent scaling, security isolation, or a vendor protocol justifies them.

Use:

- PostgreSQL/PostGIS for transactional and geospatial data.
- Private object storage for photos, audio, reports, and documents.
- Redis for distributed rate limits and short-lived coordination.
- The existing transactional outbox pattern for notifications.
- A common integration-job framework for PA, LED/VMD, social, and government adapters.
- Approved KMS or equivalent for production key lifecycle.

Do not:

- Duplicate existing auth, location, matching, public-status, or media APIs.
- Let an AI model access unrestricted internal APIs or directly close cases.
- Publish contact, identity-document, biometric, child, or sensitive case data to public channels.
- Enable a real external integration without an allowlisted destination, written authorization, and a sandbox test.

## Gate 0 — Programme mobilization and contract freeze

Gate 0 must be completed before parallel implementation accelerates.

### Engineering baseline

- Preserve and review the current uncommitted work.
- Restore a healthy disposable PostgreSQL test environment.
- Run the complete backend suite and record the result.
- Verify migrations through `0016` on a database copy.
- Regenerate and freeze the OpenAPI contract for the first client sprint.
- Define compatibility and API-versioning rules.

### Product and policy decisions

- Confirm the public product name: Kumbh Milan versus existing Kumbh Setu/KSLF identifiers.
- Approve whether volunteer and supervisor roles are required.
- Approve minimum registration fields for person cases.
- Approve claimant, witness, identity-verification, and handover fields.
- Approve help-desk visibility of handover and verification information.
- Record the approved account-free matrix: all four case types plus sightings.
- Approve grievance categories, statuses, SLA rules, and escalation ownership.
- Approve required UI and communication languages.
- Approve retention periods and biometric processing.

### External inputs

- Obtain authoritative sector, ghat, help-desk, police-station, medical-centre, and PA/display-zone data.
- Select messaging providers and obtain DLT templates and sandbox credentials.
- Obtain PA, LED/VMD, social, and government API specifications.
- Confirm staging and production environments, hostnames, network paths, and cloud ownership.

### Gate 0 exit criteria

- Full backend regression is green.
- OpenAPI is frozen for the first client release.
- Required decisions have named owners and dates.
- No synthetic location or vendor data is represented as production data.

---

## Phase 1 — Core backend and client foundation

**Priority:** Highest
**Goal:** Close the operational Lost & Found workflow before adding new public channels.

### Backend deliverables

1. **Browser API contract**
   - Allow the existing `X-Step-Up-Code` header through CORS.
   - Expose export filename, row-count, and truncation headers.
   - Preserve the strict origin allowlist.

2. **Structured reunification and handover**
   - Add encrypted claimant and approved identity-verification fields.
   - Add verification, witness, handover, rejection, correction, and completion history.
   - Link the record to both person cases and a confirmed match where applicable.
   - Prevent duplicate completion.
   - Require the approved record before person-case resolution.

3. **Custody history**
   - Record every receipt, transfer, location change, acknowledgment, and release as immutable history.
   - Expose a scoped chronological history to authorized case handlers.
   - Do not invent historical events for old cases.

4. **Production messaging**
   - Extend the existing outbox with the approved SMS/WhatsApp provider.
   - Add delivery webhooks, provider identifiers, signature validation, reconciliation, retries, and terminal failure states.
   - Keep delivery state distinct from in-app read state.

5. **Account-free public intake**
   - Support missing-person, found-person, lost-item, found-item, and sighting flows.
   - Reuse OTP verification, idempotency, private media, staff review, and non-enumerating status tracking.

6. **Mobile synchronization completion**
   - Add scope-revocation and deletion tombstones.
   - Add durable resumable attachment upload sessions.
   - Preserve checksums, quarantine, malware scanning, conflict detection, and audit.

7. **Reference and assignment completion**
   - Load approved location and item-category master data.
   - Add approved unassign, team, help-desk, bulk, and supervisor workflows.
   - Add explainable duplicate-registration candidates without automatic merging.

### Client foundation

Create separate client surfaces that share generated API contracts:

- Staff web portal.
- Citizen web portal/PWA.
- Mobile field application.
- Shared generated models and error-code mappings.

Recommended first client decision:

- Staff and citizen web: React, TypeScript, and Vite, served same-origin with the API.
- Mobile: run a short React Native versus native Kotlin/Swift spike before committing. Offline database, camera, background upload, device security, and push reliability are the decision criteria.

### Phase 1 end-to-end exit criteria

- Staff login, registration, assignment, matching, verification, handover, resolution, and audit work in the portal.
- Citizens can submit and securely track every approved account-free case type.
- Mobile users can create cases offline, resume media upload, resolve conflicts, and remove revoked local data.
- Delivery receipts prove that configured notifications were sent or failed.
- Cross-station, PUBLIC ownership, and sensitive-data tests pass.
- ClamAV and required workers are healthy in staging.

---

## Phase 2 — Public communication integrations

**Goal:** Deliver approved information through PA, LED/VMD, and social channels without duplicating reliability or security logic.

### Common integration foundation

Build one integration-job framework with:

- Draft, approval, scheduling, cancellation, dispatch, acknowledgment, and failure states.
- Idempotency, retry, timeout, circuit-breaker, reconciliation, and dead-letter behavior.
- Per-destination configuration and health.
- Payload classification and redaction.
- Immutable audit events and delivery metrics.
- Mock adapters for automated failure testing.

### PA integration

- Configurable zones, languages, categories, schedules, repeat rules, and emergency cancellation.
- Approved text templates, uploaded recordings, and optional TTS after language/provider approval.
- Vendor adapter for REST, message queue, file transfer, or another documented controller protocol.
- Never assume that a PA controller supports REST.

### LED/VMD integration

- Device registry, groups, location, resolution, orientation, capabilities, heartbeat, and active campaign.
- Text/image/approved-graphic content with priority, schedule, repeat, and expiry.
- Critical content override restricted to approved roles.
- Automatic withdrawal of expired or revoked content.

### Social-media integration

- Approved account registry and platform adapters.
- Draft, approval, schedule, publish, update, withdraw, and analytics lifecycle.
- Separate public-visibility consent from internal case visibility.
- Moderation before inbound messages or comments become sightings, notes, or grievances.

### Phase 2 exit criteria

- Each launch channel passes mock failure tests and one real sandbox test.
- Duplicate jobs cannot publish twice when the provider supports idempotency.
- Failed or offline destinations are visible without exposing sensitive payloads.
- Every publication can be withdrawn where the provider permits it.

---

## Phase 3 — AI-powered public assistance

**Goal:** Provide multilingual, grounded assistance with deterministic tools and safe human escalation.

### Phase 3A — Approval and knowledge foundation

Before model integration:

- Approve languages, provider, data residency, retention, consent, and channels.
- Approve permitted intents and emergency responses.
- Create a versioned knowledge repository for official information.
- Define human handoff, support hours, and emergency escalation.
- Build a multilingual evaluation and red-team set.
- Complete a chatbot threat model and architecture decision record.

### Phase 3B — Chat and voice implementation

- Session, message, history, feedback, language, and escalation APIs.
- Retrieval only from approved, versioned knowledge.
- Deterministic tool calls for locations, public information, guided registration, and OTP-authorized case status.
- No unrestricted SQL or internal API access.
- Speech-to-text and text-to-speech only through approved providers and retention rules.
- Accessible text fallback for every voice workflow.
- A support ticket is created when human handoff is accepted.

### Phase 3 exit criteria

- The assistant passes agreed intent, language, grounding, latency, and safety thresholds.
- It never claims a case or ticket was created when the API failed.
- It cannot confirm identity, close a case, expose unrelated records, or invent emergency instructions.
- High-risk and unsupported requests are transferred to a human.

---

## Phase 4 — Grievance and ticket management

**Goal:** Add a separate grievance domain rather than overloading Lost & Found cases.

### Deliverables

- Configurable categories, departments, priorities, SLA policies, and escalation rules.
- Citizen and staff registration channels.
- Unique ticket numbers and privacy-safe citizen tracking.
- Assignment, reassignment, comments, attachments, investigation, escalation, resolution, reopen, feedback, and immutable history.
- Existing private storage and malware scanning for attachments.
- Notification events for registration, assignment, updates, SLA warnings, breach, and resolution.
- Grievance aggregates and exports.

### Role gate

Use existing roles until a volunteer or supervisor permission matrix is formally approved. Do not silently map volunteers to help-desk users or supervisors to administrators.

### Phase 4 exit criteria

- Citizens cannot enumerate or access other tickets.
- SLA calculations use an approved timezone, calendar, pause policy, and escalation chain.
- Sensitive complaint categories receive stricter visibility.
- Attachments, audit, legal holds, and retention work end to end.

---

## Phase 5 — Government-system integrations

**Goal:** Exchange only authorized information through one adapter per approved external system.

### Per-system onboarding gate

No implementation starts until the system owner provides:

- Official API and versioning documentation.
- Written authorization and legal basis.
- Permitted data fields and source-of-truth rules.
- Authentication, signing, mTLS, and network requirements.
- Sandbox or certified emulator.
- Rate limits, timeout behavior, and support contacts.

### Adapter requirements

- Independent enable/disable configuration.
- Explicit allowlisted destinations.
- Mapping and validation at the integration boundary.
- Idempotent requests and ingest.
- Retry, circuit breaker, dead-letter, reconciliation, and manual replay.
- Schema-version monitoring.
- Redacted request/response metadata and disclosure audit.

### Phase 5 exit criteria

- Contract tests pass against the approved sandbox.
- External downtime cannot corrupt or block core case operations.
- Duplicate delivery cannot create duplicate cases or tickets.
- Disclosure history identifies what was shared, why, when, and by whom.

---

## Phase 6 — Analytics and MIS

**Goal:** Provide accurate operational intelligence without slowing transactional workflows.

### Deliverables

- Case, match, reunification, return, custody, notification, grievance, SLA, integration, station, sector, location, and day-level aggregates.
- Median and percentile handling times where approved.
- Authorized officer workload metrics with documented definitions and fairness review.
- Daily, weekly, station, sector, reunification, grievance, SLA, notification, and integration reports.
- Asynchronous CSV, Excel, and PDF generation.
- Private expiring report artifacts.
- Scheduled reports with approved recipients and complete audit.

### Dependency

Authoritative reunification metrics require structured Phase 1 handover records. Grievance and SLA metrics require Phase 4.

### Phase 6 exit criteria

- Aggregate values reconcile with equivalent scoped transactional queries.
- Date boundaries use Asia/Kolkata consistently.
- Large reports run asynchronously.
- Exports exclude unapproved PII and prevent spreadsheet formula injection.
- Representative-volume performance targets pass.

---

## Phase 7 — Security and compliance assurance

**Goal:** Complete defence in depth, privacy controls, and formal security evidence.

### Deliverables

- Approved phishing-resistant MFA programme or an explicitly accepted alternative.
- Step-up tokens bound to user, session, action, and short expiry.
- Discoverable MFA mode and enrollment state.
- Dual-read/single-write key versioning and rehearsed contact/biometric re-encryption.
- PostgreSQL RLS where it adds defence in depth, while retaining service-layer checks.
- Safe connection-pool identity context and separate worker/migration roles.
- Approved biometric policy, calibrated face threshold, demographic/quality evaluation, and breach procedure.
- Secret manager/KMS integration.
- Dependency, container, static, dynamic, and penetration testing.
- Data-retention, legal-hold, deletion, and incident-response drills.

### Phase 7 exit criteria

- Direct app-role database queries cannot bypass approved scope.
- Key rotation completes and can resume safely after interruption.
- No biometric feature is enabled without written approval.
- Critical/high findings are closed or formally accepted by an authorized owner.

---

## Phase 8 — Production readiness and operational handover

**Goal:** Prove the agreed launch scope can be operated safely under event conditions.

### Infrastructure

- Approved cloud/on-premise target, TLS, private networking, managed database, private object storage, Redis, KMS/secrets, backups, and disaster-recovery environment.
- Multiple API replicas and supervised scan, notification, integration, report, sync-maintenance, and retention workers.
- Infrastructure as code and repeatable environment promotion.

### Verification

- Protected-branch CI with tests, migration drift, OpenAPI drift, linting, dependency checks, and image scanning.
- End-to-end staging tests across every launch client and integration.
- Load, soak, failover, queue-backlog, and degraded-provider tests.
- Backup/restore drill with recorded RPO and RTO.
- Monitoring, alerting, dashboards, log retention, and on-call escalation.

### Operational handover

- Police/help-desk/admin training and role-specific SOPs.
- UAT using real workflows but non-production personal data.
- Incident, privacy, integration, and vendor support runbooks.
- Launch checklist, rollback plan, support roster, and command structure.

### Phase 8 exit criteria

- Controlled pilot passes with no unresolved launch-blocking finding.
- Every required worker and dependency is represented in readiness checks.
- Load and recovery targets are measured, not assumed.
- Product, police operations, security, legal/privacy, infrastructure, and support owners sign off.

---

## Parallel workstreams

### Workstream A — Backend core

Phase 1 workflow, public, sync, notifications, and API contracts.

### Workstream B — Staff web

Login, dashboard, case operations, search/map, matching, reunification, custody, reports, administration, grievances, integrations, and audit.

### Workstream C — Citizen web

OTP/account flows, four approved submissions, tracking, public information, grievance, chatbot, accessibility, and multilingual content.

### Workstream D — Mobile

Secure local database, offline queue, sync conflicts, media upload, tombstones, push, device security, and field testing.

### Workstream E — Integrations

Messaging, PA, LED/VMD, social media, and government adapters.

### Workstream F — AI and knowledge

Content governance, retrieval, orchestration, multilingual evaluation, voice, and handoff.

### Workstream G — Security and platform

Infrastructure, CI/CD, observability, KMS, RLS, testing, recovery, and operational readiness.

### Workstream H — Product, police operations, legal, and GIS

Policy, permissions, master data, vendor contracts, acceptance, and UAT.

## Delivery calendar

### Twelve-week pilot scope

- **Weeks 1–2:** Gate 0, regression recovery, API freeze, environment and role decisions.
- **Weeks 2–6:** Phase 1 backend spine and staff/citizen client foundations in parallel.
- **Weeks 4–8:** Mobile MVP, messaging sandbox, custody and handover workflows.
- **Weeks 7–10:** MIS v1, pilot security, staging deployment, and UAT preparation.
- **Weeks 10–12:** End-to-end rehearsal, load/recovery tests, training, and controlled pilot.

This pilot excludes uncontracted PA/LED/social/government adapters and production AI.

### Complete programme

Plan 24–36 weeks with parallel teams. Vendor, legal, network, language-content, and government approvals may extend the calendar and must not be hidden inside engineering estimates.

## Team model

Minimum parallel team for the complete programme:

- 1 technical lead/architect.
- 3 backend engineers.
- 2 web engineers.
- 1–2 mobile engineers.
- 1 AI/knowledge engineer.
- 1 integration engineer.
- 1 platform/DevOps engineer.
- 1 security engineer or retained specialist.
- 2 QA/automation engineers.
- 1 UI/UX/accessibility designer.
- Product owner plus named police, legal/privacy, GIS, and vendor representatives.

## Mandatory end-to-end scenarios

Before production, verify at minimum:

1. A family reports a missing child without an account.
2. A help-desk operator registers a found person.
3. A police officer reviews a match and verifies a claimant.
4. A witness and complete handover are recorded.
5. Custody transfers between two police locations.
6. An authorized officer publishes and cancels an approved PA announcement.
7. An LED/VMD receives and expires emergency content.
8. A grievance is assigned, escalated on SLA breach, resolved, and tracked by the citizen.
9. A notification reaches its provider and a signed delivery receipt is reconciled.
10. A mobile device resumes an offline attachment upload.
11. A revoked user loses access to previously synchronized data.
12. A face candidate is reviewed without automatic identity confirmation.
13. A station user cannot access another station's protected records through API or direct app-role SQL.
14. An administrator generates a scoped asynchronous MIS report.
15. An external government dependency fails and recovers without data loss or duplicate records.
16. A chatbot answer is grounded, an authorized case-status lookup uses OTP, and a high-risk request reaches a human.
17. A database/object-store restore passes schema, health, and smoke checks.

## Definition of done

Every delivered feature must include:

- Approved requirements and data classification.
- API and database contract.
- RBAC, row visibility, and negative authorization tests.
- Migration upgrade/downgrade and app-role grants where applicable.
- Idempotency, concurrency, timeout, retry, and failure behavior.
- Audit and redaction verification.
- Metrics, logs, alerts, and runbook updates.
- Unit, API, integration, end-to-end, security, and performance evidence appropriate to the feature.
- Updated OpenAPI and client contracts.
- Accessibility and multilingual verification for user-facing flows.
- UAT and named operational owner.

## Decision register

The following remain programme blockers until assigned and approved:

- Product name and public identifiers.
- Volunteer and supervisor permissions.
- Minimum person-case fields.
- Reunification claimant/witness/document requirements.
- Messaging provider, DLT templates, consent, and WhatsApp scope.
- PA, LED/VMD, social, and government vendors/protocols.
- Chatbot provider, languages, data residency, voice processing, and retention.
- Grievance categories, departments, SLA calendar, and escalation chain.
- Authoritative location and zone datasets.
- Retention periods, biometric legality, and face-calibration thresholds.
- MFA mechanism and recovery procedure.
- Staging/production hosts, cloud ownership, capacity targets, RPO, and RTO.

## Execution record

- **Started:** Phase 1 browser contract.
- **Implemented in the current worktree:** CORS allows `X-Step-Up-Code` and exposes `Content-Disposition`, `X-Export-Rows`, and `X-Export-Truncated`.
- **Verification:** isolated no-database CORS contract tests.
- **Implemented in the current worktree:** scoped `CaseHistory.custody_history` with append-only custody events, chronological actor/status/location/reason data, legacy-event compatibility, and no copied acknowledgement text.
- **Custody verification:** full isolated database regression passes.
- **Implemented in the current worktree:** OTP-verified account-free submission for missing persons, found persons, lost items, and found items, with idempotent replay, optional private photos, intake-station routing, notifications, staff verification/rejection, and shared public-submit rate limits.
- **Public-intake verification:** full isolated database regression passes.
- **Implemented in the current worktree:** revision `0014` structured claimant verification and handover with encrypted claimant/witness/evidence/acknowledgement data, confirmed-match linkage, police/admin permissions, verify/reject/complete lifecycle, redacted audit history, duplicate protection, item-custody enforcement, retention anonymization, and an opt-in person-resolution gate.
- **Handover verification:** migration `0014` and lifecycle/retention behavior pass the full isolated suite.
- **Implemented and tested:** revision `0015` private archived/scope-revocation tombstones with a delivered-case ledger and deterministic independent cursor.
- **Implemented and tested:** revision `0016` resumable attachment manifests, exact-offset chunks, size/SHA-256 finalization, quarantine/scanning reuse, replay, expiry, abort, and sync-status visibility.
- **Regression result:** 413 passed, one expected real-ClamAV skip; schema at `0016`; no Alembic drift.
- **Environment recovery:** a fresh isolated `kumbh-setu-test` project was created without deleting the corrupted historical Docker data/volumes.
- **Next implementation:** provider-backed notifications after vendor/DLT approval, or the approved assignment/reference-data slice.

Detailed backend tickets and acceptance criteria are maintained in `BACKEND_ENGINEERING_BACKLOG.md`.
