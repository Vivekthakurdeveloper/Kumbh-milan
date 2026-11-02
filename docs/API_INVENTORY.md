# API inventory

Source of truth: the FastAPI application. A local import of the current source produced **81** OpenAPI operations on **61** paths. **79** of those operations are under `/api/v1`. The other two are `GET /health` and `GET /ready`. `GET /metrics` is intentionally omitted from the schema and requires `METRICS_TOKEN` when that token is set.

The previously running image was not queried. It was built before this source and its container is stopped, so its older operation count is not a current measurement.

## Public

| Method | Path | Auth |
|---|---|---|
| GET | `/health` | None. Liveness only |
| GET | `/ready` | None. Dependency names only, no secrets |
| POST | `/api/v1/auth/login` | None. Rate limited |
| POST | `/api/v1/auth/refresh` | Refresh token |

## Authenticated groups

Every other `/api/v1` operation requires a bearer access token. Role checks are in services or router dependencies.

| Group | Operations |
|---|---|
| Auth | Logout, current user, password change, MFA enroll, MFA confirm |
| Stations and users | List, create, update, assignable officers, password reset |
| Locations | Read and administer reference places |
| Case types | Create, list, read, and update missing persons, found persons, lost items, and found items; assign and change status; found-item custody |
| Shared cases | Search, nearby, map, notes, history, contact reveal, attachments, matches, notifications |
| Audit | List audit events |
| Retention | Policies, plan, approve, execute, cancel, legal holds |
| Dashboard | Summary counts inside the caller's scope |
| Reports | `GET /api/v1/reports/cases.csv` |

## Contracts that changed

- Contact reveal now requires `reason_code` instead of a free-text `reason`. The audit row stores the code.
- Item release requires `recipient_acknowledgement`.
- Found-person bodies accept `safeguarding_review_required`.
- CSV export requires `created_from` and `created_to`, refuses a range over 31 days, and returns at most 1000 rows.
- MFA routes exist and return `409 mfa_disabled` unless `MFA_MODE=totp`.

OpenAPI security requirements and 401 responses are checked by `test_openapi_documents_every_operation`.
