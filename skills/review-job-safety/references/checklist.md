# Review checklist

Use one line per finding: tag (blocker, should fix, nit), `file:line`, what goes wrong, fix.

| Check | Ask | Typical fix |
|---|---|---|
| Idempotent | What if this runs twice, or stops after the side effect? | Absolute write, unique key plus upsert, processed marker, provider idempotency key |
| Arguments | Is anything but ids and plain values passed? | Pass the id, reload, handle a missing record |
| Retries | Cap, backoff with jitter, transient errors only, exhausted handler? | Set all four |
| Transaction | Is it enqueued before the commit, or in a callback? | Enqueue after commit, or an outbox |
| Uniqueness | What if two copies run at once? | Unique lock with expiry, row lock, overlap middleware, deterministic id |
| Batching | Does it load or touch everything? | Batches with a cursor, or fan out |
| Timeouts | Does every network call have one, below the job limit? | Client timeouts, job level limit |
| Shutdown | Is a mid job kill safe? | Resumable steps |

Do not report a missing setting as a bug when a global one may cover it. Ask where it is configured.
