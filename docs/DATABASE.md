# Database report

PostgreSQL 16 and PostGIS. Migrations are linear.

| Revision | Change |
|---|---|
| 0001 | Initial schema, roles, append-only audit and history |
| 0002 | Case version and match workflow |
| 0003 | Attachment scanning |
| 0004 | PostGIS locations and case geography |
| 0005 | Legal holds and retention runs |
| 0006 | Notification outbox |
| 0007 | Worker heartbeats, found-person safeguarding flag, found-item handover fields, user TOTP columns |

`0007` only adds nullable columns and one table, except `users.totp_enabled`, which is `false` for existing rows. It does not rewrite case text. Downgrade would drop those columns. Do not downgrade a database that has stored handover text or TOTP secrets; restore a backup instead.

The isolated copy of the operational volume was upgraded from `0001` to `0007`. `alembic check` on that copy exited 0. PostGIS `geography` columns produce a SQLAlchemy recognition warning and were not reported as a new upgrade operation.

The original volume was not upgraded. Its server container was not started, so its live revision was not queried in place. The copy is the evidence that the volume, before upgrade, was at `0001`.

Application tables include stations, users, cases and four detail tables, contacts, notes, assignments, status history, matches, attachments, face embeddings, staff notifications, idempotency keys, sessions, login attempts, audit events, locations, legal holds, retention runs, notifications, and worker heartbeats.

### Face matching (revision `0008`)

- `face_embeddings`: one row per photograph. Status values `FACE_FOUND`, `NO_FACE`, `MULTIPLE_FACES`, `UNREADABLE`. The embedding is Fernet ciphertext and is never exposed by APIs. Deleted with the attachment (CASCADE) or by retention.
- `case_matches` additions: `source` may be `FACE`; `left_attachment_id`, `right_attachment_id`, `face_similarity`, `face_model`; first-approval columns for the two-person FACE rule (`first_approved_by_user_id` must differ from `reviewed_by_user_id`).
- `staff_notifications`: in-app alerts for police officers when a face candidate is generated. Unique on `(recipient_user_id, match_id, kind)`.

Archive eligibility for closed or resolved cases uses `closed_at` when set, otherwise the timestamp of the transition to `RESOLVED`. A later ordinary update does not extend the archive clock. Anonymisation still uses `archived_at`. Legal holds exclude rows. The day counts in settings are defaults, not an approved legal schedule.

Tests rebuild a database whose name ends in `_test` on an allow-listed host. They take a Postgres advisory lock and truncate that database only.
