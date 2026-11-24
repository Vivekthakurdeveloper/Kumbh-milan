# Operations runbook

## Probes

- `/health` is process liveness only.
- `/ready` is readiness. Alert when it is not 200.
- `/metrics` requires `METRICS_TOKEN` outside local development. Labels are route templates, statuses, and enum values, not names or phone numbers.

Useful series already emitted: `kumbh_http_requests_total`, `kumbh_http_request_duration_seconds`, `kumbh_auth_failures_total`, `kumbh_rate_limit_rejections_total`, `kumbh_queue_depth`, `kumbh_scan_outcomes_total`, `kumbh_notification_outcomes_total`, `kumbh_retention_actions_total`, and `kumbh_db_pool_connections`.

No numeric alert thresholds are declared here. Measure a staging baseline before paging on latency or queue depth. Page immediately on `/ready` failure, a worker heartbeat older than the configured maximum, and a backup job that did not produce a checksum.

## Backup and restore

`ops/backup-volumes.sh` writes a timestamped directory under `.backups/` and checks SHA-256. `ops/restore-volume-copy.sh` restores into a new volume and refuses the two operational names.

The 23 October 2026 copy was restored, started on port 5433, upgraded, checked, and stopped. Repeating that drill is the restore test. A logical `pg_dump` of the original server was not taken because that server was not started.

For a managed database, restore a snapshot or dump into a new instance, run `python -m app.cli schema-status`, then call `/ready`. Do not restore over the only copy.

## Retention

Plan and approve runs through the API. Two different administrators are required. Execution of anonymisation in staging or production stays on `python -m app.cli retention-execute` with `RETENTION_DATABASE_URL`. A legal hold removes the case or grievance from the plan. Dry-run planning does not change cases. `ANONYMIZE_CLOSED_GRIEVANCES` follows the same rule: it cannot be executed through the API in staging or production.

## Encryption key rotation

1. Rehearse on a restored copy first.
2. Generate a new Fernet key. Set it as `CONTACT_ENCRYPTION_KEY` (or `FACE_EMBEDDING_ENCRYPTION_KEY`) and move the old key into `CONTACT_ENCRYPTION_PREVIOUS_KEYS` (JSON list). Restart the API and every worker so all processes read both keys.
3. Run `python -m app.cli rotate-encryption` (dry run) and keep the counts. Then run `python -m app.cli rotate-encryption --apply`. It commits per batch, so it is safe to stop and re-run.
4. Re-run the dry run. It must report `0 values; 0 unreadable` before you remove the old key from `*_PREVIOUS_KEYS` and restart again.
5. If any rows are reported unreadable (exit code 1), do not remove any key. The ids are printed; the key that wrote them is missing from configuration.

Keys never go into the database, logs, or audit events. The audit event records counts per column only.

## Incident response

1. Keep `/ready` output and the request ID. Do not copy contact details into the ticket.
2. If authentication is failing, check lockout and rate-limit metrics before resetting accounts.
3. If objects are missing, run reconciliation in report mode. It does not delete rows.
4. If the schema check fails, stop writes and restore the last verified backup into a new database. Do not downgrade in place after handover or MFA data exists.
5. Worker crash: the process supervisor restarts it. Rows left in `SENDING` or `SCANNING` are released by the existing stale-claim logic on the next loop.

## Maintenance

`purge-expired` is included in the maintenance worker. `rematch` and `link-locations` stay manual. `link-locations` is a dry run unless `--apply` is passed.
