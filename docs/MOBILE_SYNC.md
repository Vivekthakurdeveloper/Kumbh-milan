# Mobile offline synchronization

Backend-only support for Android and iOS staff apps that register cases offline and sync when connectivity returns. Existing `/api/v1` case, note, attachment, and auth APIs remain the primary surface; the sync endpoints batch offline work and provide incremental download.

## Authentication and scope

- Staff sign in with `POST /api/v1/auth/login` (same JWT access + rotating refresh tokens).
- Every sync call requires `Authorization: Bearer <access_token>`.
- Station visibility is identical to the rest of the API (`case_visibility_clause`). A device cannot push or pull cases outside the caller's permitted stations.
- Device registration is implicit: the first push, pull, or status call upserts a `sync_devices` row for `(user_id, device_id)`.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/sync/push` | Submit offline creates, updates, and notes |
| GET | `/api/v1/sync/pull` | Download changed cases plus archived/scope-revocation tombstones |
| GET | `/api/v1/sync/status` | Last successful sync times and server cursor |
| POST | `/api/v1/sync/attachments/sessions` | Create or resume an attachment upload manifest |
| PUT | `/api/v1/sync/attachments/sessions/{id}/content` | Append bytes at the current server offset |
| POST | `/api/v1/sync/attachments/sessions/{id}/finalize` | Verify checksum and enter quarantine/scanning |
| DELETE | `/api/v1/sync/attachments/sessions/{id}` | Abort an incomplete upload |

Assigned cases continue to use `GET /api/v1/cases?assigned_to_user_id=<me>` (user id from `/api/v1/auth/me`). Search, notes, and attachments keep their existing routes.

## Push

```http
POST /api/v1/sync/push
Authorization: Bearer …
Content-Type: application/json
Idempotency-Key: optional-batch-key   # optional; per-record client_record_id is the durable guard

{
  "device_id": "android-pixel-7-a1b2",
  "client_id": "install-uuid",
  "pending_count": 4,
  "failed_count": 0,
  "records": [
    {
      "client_record_id": "case-local-001",
      "operation": "CREATE_CASE",
      "client_created_at": "2026-11-05T10:00:00+00:00",
      "case_type": "MISSING_PERSON",
      "data": {
        "person_name": "Unknown",
        "approximate_age": 7,
        "clothing_description": "red kurta",
        "last_seen_location": "Har Ki Pauri",
        "urgency_level": "CRITICAL"
      }
    },
    {
      "client_record_id": "note-local-001",
      "operation": "ADD_NOTE",
      "client_created_at": "2026-11-05T10:01:00+00:00",
      "case_client_record_id": "case-local-001",
      "data": { "note_text": "Reporter waiting at help desk" }
    },
    {
      "client_record_id": "upd-local-001",
      "operation": "UPDATE_CASE",
      "client_created_at": "2026-11-05T10:05:00+00:00",
      "case_id": "559a68ce-bb0f-4f19-b929-4eba1ccb2b08",
      "base_version": 2,
      "data": { "clothing_description": "red kurta and blue shorts" }
    }
  ]
}
```

Response (HTTP 200 even when some records fail):

```json
{
  "device_id": "android-pixel-7-a1b2",
  "synced": 2,
  "duplicates": 0,
  "conflicts": 0,
  "failed": 1,
  "server_time": "2026-11-05T10:10:00+00:00",
  "results": [
    {
      "client_record_id": "case-local-001",
      "operation": "CREATE_CASE",
      "outcome": "SYNCED",
      "case_id": "…",
      "case_number": "KSLF-MP-2026-000042",
      "version": 1,
      "server_updated_at": "…"
    }
  ]
}
```

Per-record outcomes:

| Outcome | Meaning |
|---|---|
| `SYNCED` | Created or applied |
| `DUPLICATE` | Same `client_record_id` (or idempotency key) already applied; returns original ids |
| `CONFLICT` | `UPDATE_CASE` with stale `base_version`; nothing written; `case` holds the current server record |
| `FAILED` | Validation, permission, or station-scope error; other records still processed |

### Conflict resolution

1. Read `result.case` (or `GET /cases/{id}`).
2. Merge locally if needed.
3. Push a **new** `UPDATE_CASE` with the current `base_version`, or call `PATCH /cases/{type}/{id}` with `expected_version`.
4. Never silently overwrite a newer server record. Audit history is preserved.

### Retries

- Every offline create must send a stable `client_record_id` and `device_id`.
- Repeating the same push returns `DUPLICATE` and the original server case id; it never creates a second case.
- Direct REST creates also accept the same sync fields and `Idempotency-Key`.

## Pull

```http
GET /api/v1/sync/pull?device_id=android-pixel-7-a1b2&cursor=&limit=50
Authorization: Bearer …
```

```json
{
  "items": [
    {
      "case": { "id": "…", "case_number": "…", "case_type": "MISSING_PERSON", "version": 2, "details": { } },
      "server_updated_at": "2026-11-05T10:09:00+00:00",
      "version": 2
    }
  ],
  "next_cursor": "eyJ0Ijoi…",
  "has_more": true,
  "tombstones": [
    {
      "case_id": "559a68ce-bb0f-4f19-b929-4eba1ccb2b08",
      "reason": "SCOPE_REVOKED",
      "server_updated_at": "2026-11-05T10:09:30+00:00"
    }
  ],
  "next_tombstone_cursor": "eyJ0Ijoi…",
  "has_more_tombstones": false,
  "server_time": "2026-11-05T10:10:00+00:00"
}
```

- Keyset pagination on `(sync_changed_at, id)`.
- Notes and attachment changes bump `sync_changed_at` via database triggers (case `version` is not bumped, so note uploads do not cause false update conflicts).
- When `server_updated_at` changes, refresh notes and attachments with the existing case endpoints.
- `SYNC_PULL_LAG_SECONDS` (default 5) withholds very recent writes so a late-committing transaction is never skipped. Tests set it to 0.

### Tombstones

- The server records only cases previously returned to that user. Users never receive tombstones for cases they did not possess.
- `ARCHIVED` and `SCOPE_REVOKED` contain only `case_id`, reason, and timestamp—no new station, assignee, case fields, or PII.
- Case and tombstone streams have independent keyset cursors. Process tombstones first, then apply returned case items; persist both cursors.
- Repeating the same cursor returns the same tombstones. Deleting a local case by id is therefore idempotent.
- Scope is reconciled at pull time, covering station/role/assignment/match changes without relying on a client to fetch a now-forbidden case.

## Status

```http
GET /api/v1/sync/status?device_id=android-pixel-7-a1b2&pending_count=2&failed_count=0
```

`pending_count` and `failed_count` are stored as reported by the client. The server never invents them. `server_cursor` is the current tip of the caller's permitted cases.

## Offline attachments

Push the offline case first and read `case_id` from the result. Small online uploads may still use the original one-shot endpoint:

```http
POST /api/v1/cases/{case_id}/attachments
Authorization: Bearer …
Idempotency-Key: optional
Content-Type: multipart/form-data

media_type=PHOTO
device_id=android-pixel-7-a1b2
client_record_id=photo-local-001
client_created_at=2026-11-05T10:02:00+00:00
file=@child.jpg
```

Retries with the same `client_record_id` (or `Idempotency-Key`) return the same attachment.

For interrupted/offline uploads:

1. `POST /sync/attachments/sessions` with case/device/client ids, media type, expected byte size, SHA-256 checksum, filename, and declared content type.
2. The same manifest returns the existing session (`Idempotent-Replayed: true`).
3. `PUT /sync/attachments/sessions/{id}/content?offset=<bytes_received>` with a multipart `file` chunk. The offset must exactly match the server response.
4. Repeat until status is `READY`.
5. `POST /sync/attachments/sessions/{id}/finalize`.

Finalize rechecks size and checksum, then uses the normal signature validation, quarantine, malware scan, private storage, audit, and `client_record_id` dedupe path. A checksum mismatch moves the session to `FAILED` without creating an attachment. Finalize retries return the same attachment. Open/failed sessions are listed by `/sync/status`; expired incomplete sessions are purged by the maintenance worker.

Downloads remain audited short-lived URLs from `GET /cases/{id}/attachments/{attachment_id}`. Binary data is intentionally never embedded in `/sync/push`.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `SYNC_MAX_BATCH` | 50 | Max records per push (also caps pull page size) |
| `SYNC_PULL_LAG_SECONDS` | 5 | Pull lag window |
| `SYNC_ATTACHMENT_SESSION_TTL_HOURS` | 72 | Expiry for incomplete staged uploads |
| `SYNC_ATTACHMENT_MAX_OPEN_SESSIONS_PER_DEVICE` | 20 | Per-user/device exhaustion limit |

## Audit

- `SYNC_PUSH` — one event per batch (counts and per-record outcomes)
- `SYNC_PULL` — case/tombstone cursors, returned counts, and pagination state
- `SYNC_CONFLICT` — each conflicting update
- `SYNC_TOMBSTONE_EMITTED` — an archived or scope-revoked case previously delivered to the user
- `SYNC_ATTACHMENT_SESSION_CREATED`, `SYNC_ATTACHMENT_SESSION_FINALIZED`, `SYNC_ATTACHMENT_SESSION_ABORTED`
- Domain events (`CASE_CREATED`, `CASE_UPDATED`, `NOTE_ADDED`, `ATTACHMENT_UPLOADED`) continue to be written by existing services

## Migration

```bash
cd backend
alembic upgrade head
```

Revision `0009` adds nullable sync columns, `sync_changed_at`, triggers, unique indexes, and `sync_devices`. Revision `0015` adds the delivered-case ledger and append-only tombstones. Revision `0016` adds resumable attachment sessions. All are additive for existing case/mobile clients.

## Remaining gaps (honest)

- `Idempotency-Key` for non-sync REST clients still expires after `IDEMPOTENCY_TTL_HOURS` (default 24). Durable offline identity is `client_record_id`.
- Notes and attachment bodies are not embedded in pull responses; clients fetch them with existing endpoints.
- Case and tombstone streams use two cursors; upgraded clients must persist and drain both.
- Attachment chunks currently rewrite the bounded staging object rather than using provider-specific S3 multipart upload. This keeps the contract portable and is acceptable under the configured 10 MB photo / 25 MB audio limits.
