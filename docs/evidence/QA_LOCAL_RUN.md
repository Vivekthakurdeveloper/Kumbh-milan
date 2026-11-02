# Local QA pass — live HTTP functional testing (not pytest)

**Date:** 2026-10-23
**Scope:** Every API operation in `app.main.app` (61 paths / 81 operations), exercised as real HTTP requests
against a running server, the way a QA engineer would do manual/scripted exploratory testing — happy paths,
RBAC/permission denials, validation errors, state-machine edge cases, idempotency, optimistic concurrency,
and audit side effects.

**Result: 144/144 checks passed, 0 failed**, run against a disposable stack. See `qa/api_smoke.py` for the
harness; raw run output is not committed (it contains JWTs and request IDs), but the script is idempotent-safe
to re-run at any time.

## Environment — isolated, disposable, never touching operational data

This run used **Compose project `kumbh-setu-qa`**, not `kumbh-setu`:

```
docker compose -p kumbh-setu-qa up -d postgres redis objectstore
docker compose -p kumbh-setu-qa --profile ops run --rm migrate     # fresh DB, 0001 → 0007
docker compose -p kumbh-setu-qa up -d --build storage-init api
python3 -m app.cli create-admin --username qa_admin ...
python3 qa/api_smoke.py http://127.0.0.1:8000
docker compose -p kumbh-setu-qa down -v                            # full teardown after the run
```

This created its own volumes (`kumbh-setu-qa_pgdata`, `kumbh-setu-qa_objectdata`) and containers
(`kumbh-setu-qa-postgres-1`, `-redis-1`, `-objectstore-1`, `-api-1`), entirely separate from the operational
`kumbh-setu_pgdata` / `kumbh-setu_objectdata` volumes, which stayed on Alembic revision `0001` and whose
containers (`kumbh-setu-postgres-1`, `kumbh-setu-api-1`, `kumbh-setu-objectstore-1`) remained `Exited (137)`
— never started — for the entire session, confirmed by `docker ps -a` before and after. The disposable
project was torn down with `down -v` once testing finished, so no leftover state persists.

## What was exercised

- **Liveness/readiness**: `/health`, `/ready` (database/schema/object_storage checks), `/openapi.json`, `/metrics`.
- **Auth**: bad-password rejection, successful login, `/auth/me`, account lockout after 5 bad attempts (429),
  refresh rotation, reuse-detection session revocation, unauthenticated access rejection, MFA enroll disabled
  by default (409 `mfa_disabled`), change-password (old password invalidated, all sessions revoked), logout.
- **Admin**: stations (create/list/patch, invalid code pattern), users (create/list/patch, assignable officers,
  admin-driven password reset, duplicate username/badge, weak password rejection), locations (ghats/sectors/CRUD).
- **All four case types**: create/list/get/patch, required-field and range validation, station auto-scoping,
  cross-station write rejection (`station_scope`), assign, full status state machine, idempotency-key replay,
  optimistic-concurrency `409 case_changed` on stale `expected_version`, invalid transition rejection.
- **Found-item custody**: release blocked without `recipient_acknowledgement` (422), transfer, and the
  `custody_not_released` → `verification_required` → resolved sequencing.
- **Safeguarding**: a found-person case flagged `safeguarding_review_required=true` is blocked from `RESOLVED`
  with `safeguarding_evidence_required` when no evidence is given at all — checked **before** the generic
  `verification_required` rule — then resolves once evidence is recorded, then closes.
- **Matching**: discovered that the deterministic engine runs synchronously, best-effort, after case
  create/update (so visually similar lost/found items were already linked automatically); exercised the
  duplicate-match rejection, confirmation by a non-proposer, separation-of-duties rejection (proposer cannot
  confirm their own manually-proposed match), revoke-back-to-ASSIGNED, out-of-scope 404 on `/matches`, and that
  in-scope viewers see the structured score/reasons while the rationale field is manual-proposal-only.
- **Notes**: operational notes visible to all roles; sensitive notes restricted to `note:sensitive` (police/admin).
- **Contact reveal**: structured `reason_code` (not free text) required and enum-validated; help-desk denied,
  police allowed, decrypted value matches what was recorded at intake.
- **Attachments**: JPEG upload (`SKIPPED_DEV` scan status in local/dev), disallowed content-type → 415,
  list/download (short-lived signed URL)/soft-delete.
- **Search/geo**: full-text search, nearby-by-radius, map bounding box, and the 0.5°-span cap (422 `bbox_too_large`).
- **History**: status_history + assignments present and ordered.
- **Notifications**: queue (202), validation (422), list, recipient masked in the API response.
- **Audit**: admin-only `audit:read`, non-admin denied (403).
- **Retention & legal holds**: policy catalogue, non-admin denied, plan (dry run), same-admin approval blocked
  (`two_person_rule`), different-admin approval, execute (step-up is a no-op since `MFA_MODE=disabled`), cancel,
  legal hold place/list/release/double-release rejection.
- **Dashboard summary**: admin (global) and help-desk (station-scoped).
- **Reports**: `cases.csv` export with `X-Export-Rows`/`X-Export-Truncated` headers, the 31-day range cap
  (422 `range_too_large`), and station-scoped export for help-desk.

## Real findings worth recording (all confirmed as *correct* behaviour, not defects)

1. **Refresh-token reuse revokes the whole session**, not just the replayed token — the freshly-rotated pair
   issued moments earlier is also invalidated. This matches the documented intent ("presenting an
   already-rotated token revokes the whole session") and was the one place the test harness itself had to be
   corrected to stop assuming the new pair survived.
2. **Non-admin `station_id` overrides are hard-rejected** (`403 station_scope`), not silently ignored — stricter
   than the harness initially assumed, and better for audit clarity.
3. **Automatic matching runs synchronously on case create/update**, not only on-demand via
   `POST /matches/generate`. Two similar lost/found items were linked by the engine before the harness ever
   called propose or generate itself.
4. **Match `rationale` is a manual-proposal-only field** — automatic matches carry no free-text rationale by
   design; their explanation is the structured `score`/`confidence_band`/`reasons`, which is what the
   in/out-of-scope redaction actually gates.
5. **Safeguarding and generic verification both key off "is `verification_evidence` present at all"**, not its
   content — the safeguarding check is additionally gated on the operator flag and runs first, which is the
   behaviour the earlier ordering fix (this session) was meant to produce, now confirmed end-to-end over real HTTP.
6. **Login is rate-limited at 30/min per IP** (`rate_limit_login_per_minute`) using an in-memory backend that
   persists for the API process's lifetime — rapid repeated manual test runs from one machine can legitimately
   trip `429 account_temporarily_locked`/`too_many_requests`; this is the abuse-prevention control working, not
   a flake, and it meant each iteration of this harness needed to wait out the window.

No defects were found in this pass; the three rounds of failures while building the harness were all due to the
harness assuming the wrong API/response shape or the wrong precondition, not backend bugs — each is recorded
above once corrected, since they reveal real (correct) backend behaviour.

## Not covered by this pass

- Real SMS/WhatsApp delivery (no provider is implemented/approved — stays `BLOCKED`, see `docs/REMAINING_WORK.md`).
- Load/concurrency at scale (`load/cases.js` exists but is `NOT_VERIFIED`, see `docs/READINESS.md`).
- ClamAV malware scanning (disabled in this environment; `SCAN_MODE=disabled` is the documented local default).
