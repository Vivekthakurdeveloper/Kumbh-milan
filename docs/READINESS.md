# Readiness assessment

Statuses are only `PASS`, `FAIL`, `BLOCKED`, or `NOT_VERIFIED`.

| Tier | Status | Basis |
|---|---|---|
| Local development | PASS | Disposable Postgres on port 5434: 222 passed, 1 skipped, 0 failed. The rebuilt image is Python 3.12.15 and expects schema `0007`. |
| Shared development | NOT_VERIFIED | The shared operational volume is still at the pre-upgrade revision represented by the copy (`0001`). It was not migrated. Workers were not started there. |
| Internal demonstration | BLOCKED | Demonstration needs the operational database at revision `0007` and a decision to start the API against it. |
| Controlled pilot | BLOCKED | No approved MFA programme, notification provider, retention periods, or ClamAV deployment. |
| Production | BLOCKED | No approved cloud target, no hosted CI run, no measured capacity, and the original database has not been upgraded. |

`/ready` returning 200 is not treated as production readiness.

## Phase gates

- Phase 1: backup, copy restore, copy upgrade to `0007`, and schema check passed. Original volume was not modified.
- Phase 2: case, match, search, custody, and safeguarding tests passed on the disposable database.
- Phase 3: worker heartbeats, mock notification failure, retention clock, and export tests passed. Real provider delivery is blocked.
- Phase 4: authorization, reason codes, TOTP step-up, and production-settings tests passed. Phishing-resistant MFA is blocked rather than claimed.
- Phase 5: the full disposable suite passed. Hosted CI, load, and deployment remain unverified or blocked as listed above.
