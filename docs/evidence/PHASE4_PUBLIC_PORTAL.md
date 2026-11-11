# Phase 4 delivery report — Public citizen portal (backend)

Backend only. No Android, iOS, or web UI work.

## 1. Existing functionality reused

- FastAPI / SQLAlchemy / Alembic structure and `/api/v1` router conventions
- Missing-person `create_case`, case numbering, station scoping, workflow transitions
- Encrypted `CaseContact`, audit writer, idempotency helpers
- Attachment quarantine, signature validation, malware scanning, private S3 objects, staff presigned downloads
- Notification outbox claim / retry / backoff worker and Hindi/English mock-provider behaviour

## 2. New APIs implemented

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/public/otp/request` | Request OTP for report / sighting mobile verification |
| POST | `/api/v1/public/otp/verify` | Verify OTP → short-lived `X-Verified-Mobile-Token` |
| POST | `/api/v1/public/status/request-otp` | Non-enumerating status OTP request |
| POST | `/api/v1/public/status/verify-otp` | Status OTP → case-scoped JWT (`status:read`) |
| GET | `/api/v1/public/cases/{case_id}/status` | Privacy-safe bilingual public status |
| POST | `/api/v1/public/missing-persons` | Verified-mobile public missing-person intake (multipart) |
| POST | `/api/v1/public/sightings` | Unverified public sighting (+ optional photo) |
| GET | `/api/v1/public/information` | Published bilingual help / emergency / announcements |
| GET/POST/PATCH | `/api/v1/admin/public-information…` | Admin publish/unpublish workflows |
| GET | `/api/v1/sightings` | Station-scoped staff sighting list |
| GET | `/api/v1/sightings/{id}` | Sighting detail |
| GET | `/api/v1/sightings/{id}/photo` | Staff presigned sighting photo |
| POST | `/api/v1/cases/{case_id}/public-review` | Staff verify / reject public report (`case:review`) |
| POST/DELETE | `/api/v1/devices/push-token` | Staff JWT or citizen status-session push registration |

## 3. Database migrations added

- [backend/alembic/versions/20261107_0010_public_portal.py](../../backend/alembic/versions/20261107_0010_public_portal.py) (revision `0010`)
- Tables: `otp_challenges`, `public_sightings`, `push_device_tokens`, `public_information_items`
- Cases: `source` (`STAFF`/`PUBLIC`), `public_review_status`
- `case_contacts`: mobile lookup hash / hints / verification, email ciphertext, locale, consent
- `case_attachments`: exclusive `case_id` **or** `sighting_id` parent
- Notifications: `EMAIL`/`PUSH` channels, lifecycle templates, nullable requester, `subject`, `device_token_id`, wider `recipient_hint`
- `EXPECTED_SCHEMA_REVISION = "0010"`

## 4. Providers and notifications

| Channel | Adapter | Test posture | Live posture |
|---|---|---|---|
| SMS / OTP | MSG91 HTTP (`NOTIFICATION_PROVIDER=msg91`) | Mock / console / disabled | **Unverified** — needs auth key, sender ID, DLT-approved template IDs |
| Email | SMTP (`SMTP_ENABLED=true`) | Mock path exercised | **Unverified** — needs host credentials and from-address |
| Push | Firebase Admin FCM (Android + iOS via APNs in Firebase) | Mock path; optional extra `pip install '.[push]'` | **Unverified** — needs service-account JSON and APNs credentials in Firebase console |

Lifecycle templates (Hindi + English): `CASE_SUBMITTED`, `CASE_APPROVED`, `CASE_STATUS_UPDATED`, `POTENTIAL_MATCH_VERIFIED`, `CASE_REUNITED`, `CASE_CLOSED`, plus retained `CASE_REGISTERED` / `CASE_UPDATE_VISIT_DESK` and `OTP_CODE`. Outbox rows are inserted in the same transaction as the case lifecycle change via `enqueue_event` (no separate queue).

## 5. Security controls

- Purpose-bound OTP challenges: HMAC lookup hashes, hashed codes, expiry, attempt limits, resend cooldown, generic errors, no OTP/contact logging
- Dedicated `PUBLIC_SECURITY_SECRET` for citizen tokens / hashes
- Public intake routed through configured active help-desk user + station (`PUBLIC_INTAKE_USER_ID` / `PUBLIC_INTAKE_STATION_ID`)
- Idempotency keys namespaced by verified mobile hash
- Status OTP never confirms whether a case/mobile pair exists
- Public status DTO omits notes, contacts, staff identities, medical data, matches, and media
- Sightings stay `UNVERIFIED` and never change case status
- Sighting-only photos excluded from face matching; no public download
- Fail-closed public rate limits; audit/log redaction of OTPs, mobiles, emails, provider keys, push tokens

## 6. Tests executed and results

```text
cd backend && export TEST_DATABASE_URL=… && .venv/bin/python -m pytest -q --tb=line
278 passed, 1 skipped (ClamAV integration unset)
OpenAPI: 88 paths / 110 operations regenerated to docs/api/openapi.json
```

`tests/test_phase4_public.py` covers OTP request/verify, missing-person intake + idempotent replay, public review, status OTP anti-enumeration and redacted status, sightings + staff photo access, mock SMS/email/push delivery, retry/backoff, push-token register/delete, bilingual templates, public information publish/deny, and privacy of staff review responses. Existing staff regressions remain in the full suite.

Also run:

- `python -m compileall app` — OK
- `alembic check` — no new upgrade operations
- Migration upgrade/downgrade through the pytest session fixture (`downgrade base` → `upgrade head`)
- `docs/api/tools/validate_examples.py` — **62 examples checked, 0 failed**

## 7. Production prerequisites (not claimed verified)

1. MSG91 account, sender ID, and **DLT-approved** OTP / lifecycle template IDs mapped in `MSG91_TEMPLATE_IDS`
2. SMTP credentials and approved from-address (`SMTP_*`)
3. Firebase project with Android + iOS apps; service-account JSON (`FCM_CREDENTIALS_JSON` or file); **APNs** key/cert uploaded in Firebase for iOS
4. `PUBLIC_SECURITY_SECRET`, intake user/station IDs pointing at an active help-desk operator
5. `NOTIFICATION_PROVIDER` left `disabled` or `mock` until the above are approved; then `msg91` with SMTP/FCM enabled as needed
6. Optional install: `pip install '.[push]'` on API and notification worker images for Firebase Admin
7. Live end-to-end SMS / email / push delivery against real providers — **not executed in this delivery**

## 8. Remaining gaps

- Live MSG91 / SMTP / FCM delivery against production credentials
- Legal / DLT / consent programme sign-off for citizen SMS and email
- Public web/mobile UI (out of scope for this backend phase)
- Hosted CI run of the expanded suite (workflow present; no remote runner evidence here)
