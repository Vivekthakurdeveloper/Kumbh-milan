# Pending backend requirements — implementation and approval record

This record describes the implemented backend changes and the product/data decisions that remain intentionally blocked. It must be reviewed before changing production role access or loading operational location data.

The verification counts in this file describe the earlier registration/history slice. The current worktree also contains the programme changes recorded in [PRODUCT_IMPLEMENTATION_PLAN.md](PRODUCT_IMPLEMENTATION_PLAN.md); current OpenAPI and test evidence is maintained in [TESTING.md](TESTING.md).

## Implemented

### Case PATCH name validation

`PATCH /api/v1/cases/{type}/{case_id}` rejects an explicitly supplied `null`, empty, or whitespace-only:

- `person_name` on missing-person cases;
- `item_name` on lost-item and found-item cases.

Omitting the field still preserves its current value. Unknown person names remain valid during initial missing-person registration.

Example:

```json
{
  "person_name": "   "
}
```

Response (`422`):

```json
{
  "error": {
    "code": "validation_error",
    "message": "Enter a valid value.",
    "request_id": "01J...",
    "details": {
      "field": "person_name",
      "reason": "blank_or_null"
    }
  }
}
```

### Field-level case history

The existing `CASE_UPDATED` audit event now stores one change entry per value that actually changed. The audit row is written in the same transaction as the case update and remains protected by the existing append-only database trigger and runtime-role grants.

`GET /api/v1/cases/{case_id}/history` remains backward compatible and adds `field_changes`:

```json
{
  "status_history": [],
  "assignments": [],
  "field_changes": [
    {
      "case_id": "d728fb5e-36ad-4b43-a4f1-b45b69258517",
      "field_name": "person_name",
      "previous_value": "Mohan",
      "updated_value": "Mohan Kumar",
      "updated_by_user_id": "a5057922-7693-44bf-a8f7-a234c5dc7860",
      "user_role": "HELP_DESK_OPERATOR",
      "timestamp": "2026-11-23T10:10:00Z",
      "action_type": "CASE_UPDATED"
    }
  ]
}
```

Detail fields, urgency, known-location references, and coordinate metadata are tracked. Contact updates are represented as `contact_details` because the current case contract has no standalone editable mobile-number field.

Privacy rules:

- PUBLIC citizens continue to receive status history but receive no field-change events.
- Help-desk users can see changes to ordinary case values in cases within their station scope.
- Historical contact values remain encrypted in audit storage and are returned only to roles with `case:contact_read`.
- Historical latitude/longitude values remain encrypted and are returned only to roles with `note:sensitive`.
- Existing status and assignment history is unchanged.

### Password reset backend

The backend workflow was already implemented:

- `POST /api/v1/auth/forgot-password` (`202`);
- `POST /api/v1/auth/reset-password` (`204`).

It supports registered username, email, or mobile identifiers; HMAC-hashed expiring codes; single use; superseding; failed-attempt burning; per-IP and per-account throttling; anti-enumeration responses; Argon2id password updates; and session revocation.

The missing MSG91 integration metadata is now supplied: reset SMS delivery uses `NotificationTemplate.PASSWORD_RESET` and the `otp` flow variable. Production SMS still requires an approved DLT template ID under `MSG91_TEMPLATE_IDS`.

No frontend visibility was added or changed.

### Canonical role labels

One backend mapping now provides:

- `ADMIN` → `Administrator`
- `POLICE_OFFICER` → `Police Officer`
- `HELP_DESK_OPERATOR` → `Help Desk`
- `PUBLIC` → `Citizen`

`role_label` is returned alongside `role` by authentication/profile user responses, assignable-officer responses, and the user directory used to resolve assignment/reporter identities. Existing enum values remain unchanged.

`GET /api/v1/dashboard/summary` contains aggregate counts only and has no identity/designation field. The dashboard shell must source the badge from `GET /api/v1/auth/me`; it must not synthesize a placeholder from the OpenAPI string type.

### Sector and Ghat APIs

The required APIs already existed and were retained:

- `GET /api/v1/locations/sectors`
- `GET /api/v1/locations/ghats`
- `GET /api/v1/locations/ghats?sector_id={sector_id}`
- `GET /api/v1/locations/{location_id}`
- admin-only `POST /api/v1/locations` and `PATCH /api/v1/locations/{location_id}`

List endpoints return active rows only. Ghats can be mapped to a parent sector through `sector_id`. Location coordinates are returned by all relevant location reads and are used by case map/nearby APIs when a case references `location_id`.

## Access decision held for approval

Help-desk handover-note access was not widened.

Current enforced behavior:

- `verification_evidence` from reunification/resolution status history requires `note:sensitive`;
- police and administrators have that permission;
- help-desk and PUBLIC callers receive `null`;
- sensitive free-text case notes use the same permission;
- found-item `handover_acknowledgement` is a separate current-case detail field and is not reclassified by this change.

Recommendation: keep operational verification evidence police/admin-only. Product and police operations must classify `handover_acknowledgement` before its current visibility is changed. Any approved change should be made in the central `ROLE_PERMISSIONS` policy and covered by API authorization tests, not implemented as a frontend-only rule.

## Authoritative-data blockers

No approved Haridwar Kumbh sector/ghat catalog, source document, import file, or Har Ki Pauri **gate** coordinate exists in this repository. Coordinates in tests and OpenAPI examples are synthetic/example data and are not approved production data.

Therefore this change deliberately does not:

- seed sector or ghat names;
- promote test fixtures to production master data;
- create a Har Ki Pauri gate record;
- write an approximate coordinate.

Required before data loading:

1. Product/operations supplies an approved sector/ghat list with stable codes and parent mapping.
2. GIS/operations supplies the gate-specific WGS 84 latitude/longitude and source/provenance.
3. An administrator loads the approved rows through the existing location API in a staging copy.
4. Operations verifies dropdown mapping and map placement.
5. Only then is the same reviewed dataset loaded in production.

## Schema and deployment

No database migration is required. Field history reuses the existing append-only `audit_events` JSONB payload, and `role_label` is derived from the stored role enum.

Deployment:

1. Back up the database according to `README.md`.
2. Deploy the API build; no Alembic revision is added by this change.
3. If password-reset SMS is approved, configure a real MSG91 auth key, sender, and DLT-approved `PASSWORD_RESET` template ID. Do not enable live SMS before provider validation.
4. Keep frontend password-reset visibility and handover-note UI behavior unchanged until product approval.
5. Load location master data only through the approved-data process above.

## Verification

Completed checks:

- Python 3.13 compilation of `backend/app` and `backend/tests`;
- IDE diagnostics on every changed Python file: no errors;
- generated FastAPI OpenAPI compared with `docs/api/openapi.json`: exact match (92 paths, 160 schemas);
- API documentation examples: 66 checked, 0 failed;
- direct contract checks for all blank/null/omitted name cases, protected history-value decryption, canonical role labels, and MSG91 password-reset template metadata.

The database-backed affected suites were invoked for cases, workflow, public accounts, password reset, portal contracts, geo, and platform behavior. Test setup could not connect to the disposable PostgreSQL database: the host disk was full, Docker image export failed with an I/O error, and PostgreSQL then reported a storage-file I/O error. No test body ran, so this environment cannot provide a regression verdict. Do not restart, repair, migrate, or delete the existing Docker volume without the repository recovery procedure and explicit approval.

## Files changed

Runtime:

- `backend/app/services/cases.py`
- `backend/app/services/workflow.py`
- `backend/app/schemas/cases.py`
- `backend/app/schemas/workflow.py`
- `backend/app/models/enums.py`
- `backend/app/models/organization.py`
- `backend/app/schemas/admin.py`
- `backend/app/auth/password_reset.py`
- `backend/app/notifications/__init__.py`

Tests:

- `backend/tests/test_cases_api.py`
- `backend/tests/test_workflow.py`
- `backend/tests/test_public_accounts.py`
- `backend/tests/test_password_reset.py`
- `backend/tests/test_phase1_portal.py`
- `backend/tests/test_geo.py`

Contracts and operations:

- `.env.example`
- `README.md`
- `docs/SECURITY.md`
- `docs/REMAINING_WORK.md`
- `docs/api/API_ENDPOINT_CATALOG.md`
- `docs/api/API_GAP_ANALYSIS.md`
- `docs/api/API_REQUEST_RESPONSE_REFERENCE.md`
- `docs/api/DATABASE_AND_DATA_CONTRACTS.md`
- `docs/api/FRONTEND_INTEGRATION_GUIDE.md`
- `docs/api/openapi.json`
