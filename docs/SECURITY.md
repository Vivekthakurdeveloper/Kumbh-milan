# Security report

This is an application review of the changes in this tree. It is not a penetration test or a certification.

## Authentication

Argon2id hashes passwords. Access tokens are short-lived JWTs with issuer `kumbh-setu`. Refresh tokens rotate, and reuse revokes the session. Failed logins lock the account. Password changes and resets revoke sessions. Production settings reject placeholder secrets, open CORS, in-memory rate limits, API docs, and a non-ClamAV scan mode.

`MFA_MODE=totp` adds a six-digit step-up header for admin password reset and API retention execution. The secret is encrypted with the contact Fernet key and returned only at enrollment. This TOTP implementation is not phishing-resistant. Production MFA is `BLOCKED` until an approved mechanism and recovery procedure exist. `MFA_MODE` stays `disabled` by default.

There is no public self-service password reset.

## Authorization

Roles are administrator, police officer, and help-desk operator. There is no volunteer role. Case reads outside station scope return 404. Help-desk users do not receive match explanations for a counterpart they cannot see. Police cross-station summary permission still allows those explanations because that role may search all stations.

Contact reveal requires `case:contact_read` and a reason code: `REUNIFICATION_REVIEW`, `FAMILY_ENQUIRY`, `INVESTIGATION`, or `CARE_HANDOVER`. The optional note is not stored. Exports use the same visibility clause and write `CASE_EXPORTED`.

## Privacy

Contact values are Fernet ciphertext. A single `CONTACT_ENCRYPTION_KEY` is used. There is no dual-key re-encryption path, so existing ciphertext must not be migrated to a new key until a recovery window is approved. Losing the key makes stored contacts unreadable. Logs and readiness bodies do not include connection strings or contact values. Notification templates contain the case number and station name only. The mock provider stores a masked phone.

Facial embeddings use a separate Fernet key (`FACE_EMBEDDING_ENCRYPTION_KEY`). Embeddings are never returned by public APIs; audit redaction covers `embedding` and `embedding_ciphertext`. Face photographs are used only for authorised match review. Processing biometric data for production needs privacy/legal approval. `FACE_EMBEDDING_RETENTION_DAYS` is an operational default (30), not a legally approved period. FACE candidates require two different authorised officers before confirmation; a match never automatically reunites two people.

## Files

Uploads are signature-checked, stored under generated keys, and left quarantined until a scanner promotes them. `SCAN_MODE=disabled` is not a clean result. Reconciliation reports missing objects and does not delete database rows.

## Abuse controls

The Redis rate-limit backend is shared across replicas. Compose now sets `REDIS_URL` for the API. Uvicorn is started with `--no-proxy-headers`; only CIDRs in `TRUSTED_PROXIES` may supply `X-Forwarded-For`. Request size and upload size are limited. Production disables interactive docs.

## Outstanding

| Item | Status |
|---|---|
| Phishing-resistant production MFA and recovery | BLOCKED |
| Real SMS/WhatsApp provider, DLT templates, consent approval | BLOCKED |
| Legally approved retention periods | BLOCKED |
| Key rotation of existing ciphertext | BLOCKED |
| PostgreSQL row-level security in addition to service filters | Not added; service and query filters are the enforced control |
| Hosted CI and a scanned production image | NOT_VERIFIED |
