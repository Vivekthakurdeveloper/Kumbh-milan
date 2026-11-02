# Kumbh Setu / Kumbh Milan — API documentation for frontend implementation

Backend API documentation for the lost-and-found and case-management system for Haridwar Kumbh Mela 2027. It was produced by auditing the FastAPI backend in `backend/` and is intended for the frontend team.

**Naming:** the product brief calls the product **Kumbh Milan**. The code, the OpenAPI title ("Kumbh Setu Lost & Found API"), and case numbers (`KSLF-…`) use **Kumbh Setu**. They are the same system (open question Q-1).

## Documents

| Document | Use it for |
|---|---|
| [API_ENDPOINT_CATALOG.md](./API_ENDPOINT_CATALOG.md) | Every operation (81 in OpenAPI + `/metrics`), roles, main errors, implementation status, role → permission matrix, visibility rules |
| [API_REQUEST_RESPONSE_REFERENCE.md](./API_REQUEST_RESPONSE_REFERENCE.md) | Exact request/response contracts and examples per endpoint; error codes; rate limits |
| [FRONTEND_INTEGRATION_GUIDE.md](./FRONTEND_INTEGRATION_GUIDE.md) | Token handling, API client, TypeScript types, uploads, validation mapping, permission-based UI, CORS |
| [SCREEN_TO_API_MAPPING.md](./SCREEN_TO_API_MAPPING.md) | Which endpoints each screen uses and whether the screen is Ready, Partial, or Blocked |
| [DATABASE_AND_DATA_CONTRACTS.md](./DATABASE_AND_DATA_CONTRACTS.md) | Entities, fields, enums, state machines, audit actions |
| [API_GAP_ANALYSIS.md](./API_GAP_ANALYSIS.md) | Prioritised gaps (P0–P3), defects, open questions |
| [API_TESTING_GUIDE.md](./API_TESTING_GUIDE.md) | What was verified, how to run the suites, manual test cases |
| [openapi.json](./openapi.json) | OpenAPI 3.1 document generated from the code (import into Swagger UI, Postman, or `openapi-typescript`) |
| [tools/validate_examples.py](./tools/validate_examples.py) | Checks every marked JSON example against the backend's Pydantic models |

## Getting started (frontend)

1. Read the common rules in the reference §0: base path `/api/v1`, bearer tokens, error envelope, pagination, `Idempotency-Key`, optimistic locking.
2. Generate types: `npx openapi-typescript docs/api/openapi.json -o src/api/schema.d.ts`.
3. Implement the client, single-flight token refresh, and error mapping from the integration guide §2–§4.
4. Build screens in the order of the readiness table in the screen mapping; avoid building Blocked screens.
5. Run against a local stack (`docker compose up`, see the repository `README`/`docs/`), never against shared data.

## Key facts

- **Stack:** FastAPI, PostgreSQL 16 + PostGIS, private S3-compatible storage with short-lived presigned URLs, Redis rate limiting, ClamAV scanning, background workers.
- **Roles:** `ADMIN`, `POLICE_OFFICER`, `HELP_DESK_OPERATOR`. UI gating should use `GET /auth/me` → `permissions`.
- **Case types:** missing person, found person, lost item, found item. Each has its own endpoints under `/api/v1/cases/{type}`; shared operations live under `/api/v1/cases/{case_id}/…`.
- **Matching** is deterministic and rule-based (not AI, no photo comparison). Confirmation requires police or admin, with separation of duties for manual proposals.
- **Notifications** are an SMS/WhatsApp outbox to families. No provider is integrated, so delivery is blocked (gap G-9). There are no staff in-app notifications.

## Status summary

- 81 OpenAPI operations: 80 implemented with recorded test evidence, 1 implemented with no test found (`GET /users/{user_id}`). Of those, the family notification endpoint is blocked for delivery and the dashboard and CSV export are partial.
- Screens: 10 Ready, 10 Partial, 2 Blocked.
- Two defects found by inspection: `language` length mismatch causing `500` (G-15, P0) and `CaseSummary.location_id` always `null` (G-14).

## Assumptions

- No frontend exists in the repository. React + TypeScript (Vite) is assumed because the example CORS origin is `http://localhost:5173`.
- Implementation status is based on code inspection, the generated OpenAPI, and the project's recorded test runs. The database-backed suites were **not re-run** during this exercise because Docker was not running (see the testing guide §1).
- All example payloads are **sample data**. Names, ids, phone numbers, and case numbers are invented and validated only for shape.

## Scope of this exercise

- No application code, database schema, routes, or business logic were changed. Only files under `docs/api/` were added.
- The existing project documents in `docs/` (for example `API_INVENTORY.md`, `WORKFLOWS.md`, `TESTING.md`) were left unchanged; where they differ from this folder, the code is the source of truth.
- No secrets were read or copied: `.env` was not opened, and the documents reference settings by name only.
