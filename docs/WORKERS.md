# Worker operations

Workers are OS processes. A CLI command that is not supervised is not a running worker. Compose profile `workers` defines three services. They were not started against the operational database.

## Processes

| Command | Heartbeat | Work | Retry |
|---|---|---|---|
| `python -m app.cli scan-worker` | `scan` | Claims quarantined attachments | Existing scan backoff, then `SCAN_FAILED` |
| `python -m app.cli notifications-worker` | `notifications` | Delivers due outbox rows | Bounded exponential backoff, then `FAILED` |
| `python -m app.cli maintenance-worker` | `cleanup` and `retention` | Purges expired idempotency keys, login attempts, and sessions; reports missing objects | A failed pass records `degraded` and the next loop retries |

`retention-execute` remains an explicit command for an already approved run. The retention heartbeat means the maintenance process is alive, not that anonymisation ran.

## Behaviour

- SIGINT and SIGTERM stop the loop after the current batch.
- Heartbeats are upserts in `worker_heartbeats`. A database error is logged as the exception type and does not kill the loop.
- `/ready` treats a worker as missing when `WORKERS_REQUIRED=true` and the row is absent, not `ok`, or older than `WORKER_HEARTBEAT_MAX_AGE_SECONDS` (default 180).
- Notification delivery is idempotent on the outbox key. The mock provider can raise a temporary `ProviderError`; the row stays retryable.
- Do not point these processes at the operational database until the schema there is at `0007` by an approved migration.

## Local start

```bash
docker compose --profile workers up -d scan-worker notifications-worker maintenance-worker
```

That command uses the Compose database. It was not run as part of this change.
