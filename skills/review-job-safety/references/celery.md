# Celery (Django)

## Arguments
- Pass primary keys and plain values. The default serializer is JSON, so model instances do not serialize, and pickle should not be used for untrusted input.
- Reload inside the task: `Order.objects.get(pk=order_id)`, and handle `DoesNotExist` (the task can run before the commit, see below).

## Retries (documented defaults)
- `autoretry_for`: no exceptions are autoretried by default. List transient exceptions only, not `Exception`.
- `max_retries`: default 3. `None` retries forever.
- `retry_backoff`: default `False`, meaning autoretries are not delayed. Set `retry_backoff=True` (or a number as the factor). `retry_backoff_max` defaults to 600 seconds and `retry_jitter` defaults to `True`.
- `default_retry_delay`: three minutes, used by `self.retry()` when no `countdown` is given.
- `retry_kwargs={'max_retries': 5}` caps autoretries.
- Do not call `.delay()` from an `except` block to retry: use `self.retry(exc=exc, countdown=...)`.

## Enqueue and transactions
- Inside `transaction.atomic()` the worker may run before the commit. Use `transaction.on_commit(lambda: task.delay(pk))`, or `task.delay_on_commit(pk)`: the Celery docs say it was added in Celery 5.4. Confirm your Celery version.
- `ATOMIC_REQUESTS = True` makes every request a transaction, so a `.delay()` in a view is inside one.

## Idempotency and redelivery
- `acks_late=True` acknowledges the message after the task finishes (the default is to acknowledge before running), so a worker crash re-runs the task. That makes idempotency mandatory. Related: `reject_on_worker_lost`.
- With a Redis broker, a message not acknowledged within the `visibility_timeout` transport option is delivered again, so a task that outlives it can run twice. Check the Celery docs for the default of your version.
- Relative updates (`F('n') + 1`) apply twice on a rerun. Use a processed marker, a unique constraint, or `get_or_create`.

## Uniqueness and locking
- Celery has no unique tasks. Use a lock with an expiry (for example a cache `add` with a timeout), `select_for_update()` inside `transaction.atomic()`, or a unique database constraint. Decide whether a skipped duplicate is dropped or retried.

## Long tasks
- `Model.objects.all()` in a loop loads the whole queryset into memory when evaluated; use `.iterator(chunk_size=...)` and process by pk ranges, or fan out one task per range.

## Timeouts
- `time_limit` hard kills the task process; `soft_time_limit` raises `SoftTimeLimitExceeded` inside the task so it can clean up. Both are task options, and workers have a global setting (`task_time_limit`). The docs say the worker default is used when not set.
- `requests` has no default timeout: pass `timeout=(connect, read)`.
