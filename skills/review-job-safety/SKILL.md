---
name: review-job-safety
description: This skill should be used when the user writes, reviews or debugs background jobs, workers or queued tasks, for example "Sidekiq", "Active Job", "perform_async", "perform_later", "retry_on", "Celery", "shared_task", "autoretry_for", "Laravel queue", "ShouldQueue", "ShouldBeUnique", "BullMQ", "queue.add", or when a job runs twice, runs before the record exists, retries forever, floods the queue after an outage, hangs a worker, or loads a whole table. Checks idempotency, arguments, retries, transactions, uniqueness, batching and timeouts, and proposes the fix.
version: 1.0.0
---

# Review Job Safety

A job runs at least once, not exactly once. It can run twice, late, out of order, in parallel with itself, against data that changed, and it can die halfway. Review each job for what happens in those cases.

## The checks

1. **Idempotent.** Running it twice (retry, duplicate enqueue, worker crash after the side effect but before the ack) must leave the same result. Look for relative updates (increment, `F() + 1`), inserts without a unique key, emails and payments with no sent marker or idempotency key. Fix: an absolute write, a unique constraint plus upsert, a processed marker checked in the same transaction as the write, or the provider's idempotency key.
2. **Ids, not objects.** Pass ids and reload inside the job. A full record or attributes hash goes stale while queued, bloats the payload, and may not serialize. Active Job and Laravel `SerializesModels` store a reference and reload, which is fine; Sidekiq and Celery arguments must be plain JSON values.
3. **Bounded retries with backoff.** Every retry policy needs a cap, a growing delay with jitter, and a list of transient errors only. Unbounded or immediate retries turn one outage into a retry storm. Decide what happens when retries are exhausted (dead set, failed table, alert).
4. **Enqueue after commit.** A job enqueued inside a transaction can start before the commit and not find the row, or run after a rollback. Enqueue after the commit, or write the job intent to the database in the transaction and enqueue from it (outbox). Callbacks like `after_create` and `after_save` run inside the transaction.
5. **Uniqueness and locking.** Say what happens if two copies run at once. Options: a unique job lock with an expiry, a row lock (`with_lock`, `select_for_update`), an overlap-prevention middleware, or a deterministic job id. Every lock needs an expiry and a decision for the skipped run (drop or retry).
6. **Batching.** A long job must process in bounded batches, be resumable (store a cursor or fan out one job per batch), and survive a deploy that stops the worker mid way. Never load a whole table.
7. **Timeouts.** Every network call needs its own timeout, shorter than the job and queue-level limits. Know the job level limit of the queue library and the redelivery window of the broker, because a job that outlives it runs twice.
8. **Shutdown and redelivery.** Check what happens when the worker is killed mid job: is the job re-run, from the start? Is step one safe to repeat (see check 1)?

## Workflow

1. Find the job and every call that enqueues it, including callbacks and other jobs.
2. Walk the eight checks and write what goes wrong in production for each failure, or mark it as handled with the place.
3. List settings you cannot see (global retry, worker timeout, broker redelivery) as questions, never as facts.
4. Propose the fix with its cost, in the project's framework.

Per stack detail: `references/sidekiq-active-job.md`, `references/celery.md`, `references/laravel.md`, `references/bullmq.md`. The checklist for a review comment is `references/checklist.md`.

## The scanner

`scripts/job_safety_scan.py` is a deterministic heuristic scan of `.rb`, `.py`, `.php` and JS/TS files. Rule ids: `JOB-TXN-ENQUEUE`, `JOB-OBJECT-ARG`, `JOB-RETRY-UNBOUNDED`, `JOB-RETRY-BROAD`, `JOB-RETRY-NO-BACKOFF`, `JOB-NO-TIMEOUT`, `JOB-HTTP-NO-TIMEOUT`, `JOB-NO-BATCH`, `JOB-NON-IDEMPOTENT`, `JOB-SELF-REENQUEUE`. Add `job-safety: ok` on a line to suppress it. It skips environment, credential, secret, database, queue config and `settings*.py` files, so a global timeout or retry setting there is invisible to it. It cannot see uniqueness, locking or idempotency designed elsewhere, so every hit is a lead and a clean run proves nothing.
