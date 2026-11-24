# Worker operations

Workers are OS processes. A CLI command that is not supervised is not a running worker. Compose profile `workers` defines four worker services plus `clamav`. They were not started against the operational database.

## Processes

| Command | Heartbeat | Work | Retry |
|---|---|---|---|
| `python -m app.cli scan-worker` | `scan` | Claims quarantined attachments | Existing scan backoff, then `SCAN_FAILED` |
| `python -m app.cli notifications-worker` | `notifications` | Delivers due outbox rows | Bounded exponential backoff, then `FAILED` |
| `python -m app.cli maintenance-worker` | `cleanup` and `retention` | Purges expired auth/idempotency rows and resumable upload staging sessions; reports missing objects; purges expired face embeddings | A failed pass records `degraded` and the next loop retries |
| `python -m app.cli operations-worker` | `operations` | Outbound integrations, public alerts, MIS reports (CSV/PDF) and schedules, grievance SLA escalation | Per-item backoff; a failed pass records `degraded` |

`retention-execute` remains an explicit command for an already approved run. The retention heartbeat means the maintenance process is alive, not that anonymisation ran.

## Behaviour

- SIGINT and SIGTERM stop the loop after the current batch.
- Heartbeats are upserts in `worker_heartbeats`. A database error is logged as the exception type and does not kill the loop.
- `/ready` treats a worker as missing when `WORKERS_REQUIRED=true` and the row is absent, not `ok`, or older than `WORKER_HEARTBEAT_MAX_AGE_SECONDS` (default 180).
- Notification delivery is idempotent on the outbox key. The mock provider can raise a temporary `ProviderError`; the row stays retryable.
- The notifications worker dispatches `SMS` / `WHATSAPP` (MSG91 or mock), `EMAIL` (SMTP), and `PUSH` (Firebase FCM). Permanent FCM invalid-token responses deactivate the stored device token. Install the optional `push` extra on worker images when FCM is enabled.
- Maintenance should continue to purge expired OTP challenge rows alongside idempotency keys and sessions.
- Do not point these processes at the operational database until the schema there is at the build's expected revision (`0019`) through an approved migration.

## Local start

```bash
docker compose --profile workers up -d clamav scan-worker notifications-worker maintenance-worker operations-worker
```

That command uses the Compose database. It was not run as part of this change. `scan-worker` waits for `clamav` to become healthy, which takes several minutes on first start while signatures download. Set `SCAN_MODE=clamav` in `.env`; otherwise the worker does not use the daemon. With the daemon on `127.0.0.1:3310`, `CLAMAV_INTEGRATION_HOST=localhost` enables the real EICAR test.
