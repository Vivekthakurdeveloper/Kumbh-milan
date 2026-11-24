# Security report

This is an application review of the changes in this tree. It is not a penetration test or a certification.

## Authentication

Argon2id hashes passwords. Access tokens are short-lived JWTs with issuer `kumbh-setu`. Refresh tokens rotate, and reuse revokes the session. Failed logins lock the account. Password changes and resets revoke sessions. Production settings reject placeholder secrets, open CORS, in-memory rate limits, API docs, and a non-ClamAV scan mode.

`MFA_MODE=totp` enables TOTP enrollment and adds a six-digit step-up header for admin password reset and API retention execution. Once an account has `totp_enabled`, sign-in is two-step regardless of `MFA_MODE`: `POST /auth/login` returns only a challenge (opaque, SHA-256-hashed at rest, bound to the user and their `token_version`, 5-minute TTL, single-use under a row lock, superseded by a newer login) and no session exists until `POST /auth/mfa/verify-login` accepts a code. Wrong codes burn the challenge after `MFA_LOGIN_MAX_ATTEMPTS` and count toward the login lockout; a code's time step is never accepted twice. Confirming enrollment signs out the user's other sessions. The secret is encrypted with the contact Fernet key and returned only at enrollment. This TOTP implementation is not phishing-resistant. Production MFA is `BLOCKED` until an approved mechanism and recovery procedure exist. `MFA_MODE` stays `disabled` by default.

Active accounts can use `POST /api/v1/auth/forgot-password` and `POST /api/v1/auth/reset-password` with their username, registered email, or registered mobile number. Numeric reset codes are single-use, time-limited, superseded by a newer request, burned after repeated failures, and stored only as HMAC-SHA-256 values keyed from `JWT_SECRET`. Existing and non-existing accounts receive the same acknowledgement. Per-IP and silent per-account limits apply. SMS delivery supplies the configured notification provider with the DLT `PASSWORD_RESET` template and OTP variable; without approved provider credentials and a `PASSWORD_RESET` template ID, SMS recovery is not operational. The separate admin reset route remains available to administrators.

Citizen portal authentication uses purpose-bound OTP challenges and short-lived JWTs signed with `PUBLIC_SECURITY_SECRET` (falls back to `JWT_SECRET` only in local/test). Verified-mobile tokens are restricted to a purpose and mobile hash; status tokens are restricted to one case and `status:read`. OTP codes, mobiles, emails, and push tokens are never written to audit payloads or application logs.

Mobile scope-revocation tombstones are generated only from a per-user ledger of cases previously returned by sync. Their payload contains only case id, reason, and timestamp; it never discloses the new station, assignee, case fields, or PII.

## Staff help assistant

`POST /api/v1/assistant/ask` requires a staff session (`assistant:use`). It answers only from the reviewed knowledge base in `app/services/assistant_knowledge.py` and has no access to cases, people, contacts, or users. With `ASSISTANT_PROVIDER=llm`, questions matching no article are never sent out; others are sent with emails and digit runs redacted, and the model sees only the matched articles. Answers that are empty, in the wrong script, or echo the hidden instructions are discarded. Questions are not logged or stored; `ASSISTANT_LLM_API_KEY` is redacted from audit payloads.

## Authorization

Roles are administrator, police officer, help-desk operator, and PUBLIC citizen. There is no volunteer role. Case reads outside station scope return 404. Help-desk users do not receive match explanations for a counterpart they cannot see. Police cross-station summary permission still allows those explanations because that role may search all stations.

Structured claimant/reunification records require `handover:read` or `handover:manage`, granted only to police and administrators. Legacy status `verification_evidence` remains controlled through `note:sensitive`; help-desk operators and PUBLIC citizens receive `null`. Found-item `handover_acknowledgement` remains a separate current-case field.

Contact reveal requires `case:contact_read` and a reason code: `REUNIFICATION_REVIEW`, `FAMILY_ENQUIRY`, `INVESTIGATION`, or `CARE_HANDOVER`. The optional note is not stored. Exports use the same visibility clause and write `CASE_EXPORTED`.

## Privacy

Contact values are Fernet ciphertext. A single `CONTACT_ENCRYPTION_KEY` is used. There is no dual-key re-encryption path, so existing ciphertext must not be migrated to a new key until a recovery window is approved. Losing the key makes stored contacts unreadable. Logs and readiness bodies do not include connection strings or contact values. Case-lifecycle notification templates contain only the case number and station name; OTP/password-reset templates contain only the one-time code and generic safety text. The mock provider stores a masked phone.

Case field edits are recorded in append-only audit events in the same transaction as the edit. Ordinary case fields retain their previous and updated values for the staff history view. Historical contact values and precise coordinates are Fernet ciphertext in the audit payload and are decrypted only for roles with `case:contact_read` and `note:sensitive`, respectively. PUBLIC case history never returns field-edit events.

Structured handovers encrypt claimant, witness, identity-document reference, verification evidence, acknowledgement, and rejection reason with the contact-data Fernet key. Audit events contain lifecycle metadata and booleans, not those plaintext values. Approved retention anonymisation clears the ciphertext and handover location while retaining non-personal lifecycle evidence.

Facial embeddings use a separate Fernet key (`FACE_EMBEDDING_ENCRYPTION_KEY`). Embeddings are never returned by public APIs; audit redaction covers `embedding` and `embedding_ciphertext`. Face photographs are used only for authorised match review. Processing biometric data for production needs privacy/legal approval. `FACE_EMBEDDING_RETENTION_DAYS` is an operational default (30), not a legally approved period. FACE candidates require two different authorised officers before confirmation; a match never automatically reunites two people.

## Files

Uploads are signature-checked, stored under generated keys, and left quarantined until a scanner promotes them. `SCAN_MODE=disabled` is not a clean result. Reconciliation reports missing objects and does not delete database rows.

Resumable mobile uploads stage bounded private objects under generated session keys. Every chunk must use the exact server offset; finalize rechecks declared size and SHA-256 before invoking the same signature validation, quarantine, malware scan, and idempotency controls as one-shot uploads. Expired/aborted staging objects are deleted by maintenance.

## Abuse controls

The Redis rate-limit backend is shared across replicas. Compose now sets `REDIS_URL` for the API. Uvicorn is started with `--no-proxy-headers`; only CIDRs in `TRUSTED_PROXIES` may supply `X-Forwarded-For`. Request size and upload size are limited. Production disables interactive docs.

## Outstanding

| Item | Status |
|---|---|
| Phishing-resistant production MFA and recovery | BLOCKED |
| Live MSG91 / SMTP / FCM delivery (adapters present; credentials and DLT/APNs approval outstanding) | BLOCKED / NOT_VERIFIED |
| Real SMS/WhatsApp DLT templates and consent programme approval | BLOCKED |
| Legally approved retention periods | BLOCKED |
| Key rotation of existing ciphertext | BLOCKED |
| PostgreSQL row-level security in addition to service filters | Not added; service and query filters are the enforced control |
| Hosted CI | VERIFIED on current `main` |
| Scanned production image | NOT_VERIFIED |
