# Readiness assessment

Statuses are only `PASS`, `FAIL`, `BLOCKED`, or `NOT_VERIFIED`.

| Tier | Status | Basis |
|---|---|---|
| Local development | PASS | Fresh isolated PostgreSQL/PostGIS regression at schema `0016`: 413 passed / 1 skipped; `alembic check` reports no drift. |
| Shared development | NOT_VERIFIED | The shared operational volume is still at the pre-upgrade revision represented by the copy (`0001`). It was not migrated to `0016`, and workers were not started there. |
| Internal demonstration | BLOCKED | Backend regression is green, but demonstration still needs a reviewed staging deployment at `0016`, required workers, and client applications. |
| Controlled pilot | BLOCKED | No approved MFA programme, notification provider, retention periods, or ClamAV deployment. |
| Production | BLOCKED | Hosted backend CI passes, but there is no approved cloud target, measured capacity, recovery drill, production provider approval, or operational database upgrade. |

`/ready` returning 200 is not treated as production readiness.

## Phase gates

- Phase 1: backup, copy restore, copy upgrade to `0007`, and schema check passed. Original volume was not modified.
- Phase 2: case, match, search, custody, and safeguarding tests passed on the disposable database.
- Phase 3: worker heartbeats, mock notification failure, retention clock, and export tests passed. Real provider delivery is blocked.
- Phase 4: authorization, reason codes, TOTP step-up, and production-settings tests passed. Phishing-resistant MFA is blocked rather than claimed.
- Phase 5: the full disposable suite passed and hosted CI now passes on `main`. Load, recovery, and deployment remain unverified or blocked as listed above.
