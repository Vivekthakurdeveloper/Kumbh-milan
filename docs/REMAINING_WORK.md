# Remaining work

| Item | Status | Why it is not finished | Acceptance |
|---|---|---|---|
| Migrate the original Docker volume | BLOCKED | The volume is operational data at revision `0001` on the verified copy. Upgrade needs explicit approval after this recovery plan. | Approved window, fresh backup, upgrade, `schema-status` shows `0007` |
| Real SMS or WhatsApp delivery | BLOCKED | No vendor, DLT registration, or consent policy | Provider tests against a sandbox, no personal data in the body |
| Production MFA | BLOCKED | TOTP is available but not an approved phishing-resistant programme | Chosen mechanism, enrollment, and recovery drill |
| Legal retention periods | BLOCKED | Current day counts are code defaults (including `FACE_EMBEDDING_RETENTION_DAYS`) | Written approval, then configuration only |
| Contact-key rotation | BLOCKED | One Fernet key; rotating it without a dual-read window loses plaintext | Rehearsal on a copy |
| Face embedding key rotation | BLOCKED | One Fernet key for embeddings; same dual-read gap as contacts | Rehearsal on a copy |
| Biometric / DPDP approval for face matching | BLOCKED | Pipeline is implemented; production use needs privacy approval and calibrated threshold on real event photos | Written approval; threshold rehearsal |
| Volunteer role | Out of scope | Not approved | Separate permission matrix and tests |
| Hosted CI | NOT_VERIFIED | No Git repository or remote | A runner executes `.github/workflows/backend.yml` |
| Load capacity | NOT_VERIFIED | No approved traffic target; `load/cases.js` was not run | Measured run against an isolated environment |
| Cloud infrastructure | NOT_VERIFIED | No approved account | Terraform or the chosen host applied only after authorization |
| ClamAV in Compose | NOT_VERIFIED | No daemon in the default file; the real EICAR test stays skipped | `CLAMAV_INTEGRATION_HOST` test passes |
| Ruff or another formatter | NOT_STARTED | The tree has no lint configuration; CI compiles and tests instead | Lint job fails on a new violation without a mass reformat |
| Row-level security | NOT_STARTED | Service filters are tested; a second database policy was not added | Policies with the app role and cross-station tests |
